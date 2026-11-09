"""Sparkplug B edge node publisher (PLAN 3.5): the I/O table as one edge node's metrics over MQTT.

Behavior spec
-------------
:class:`SparkplugPublisher` connects to an MQTT 3.1.1 broker as one Sparkplug B edge node
(Eclipse Sparkplug 3.0.0) and publishes every tag of the I/O table as a node metric, for an
Ignition MQTT Engine or any Sparkplug host. It is read-only telemetry: the PLC still talks to the
simulator over Modbus TCP or OPC UA, and an NCMD writes nothing (see below).

Session, as Sparkplug 3.0 chapters 4 and 5 require:

1. CONNECT with clean session, keep-alive 30 s, and an MQTT Will: NDEATH on
   ``spBv1.0/<group>/NDEATH/<node>`` at QoS 1, not retained, whose payload holds one metric,
   ``bdSeq`` (Int64). ``bdSeq`` starts at 0 and goes up by one on every new CONNECT, wrapping 255
   to 0.
2. SUBSCRIBE to ``spBv1.0/<group>/NCMD/<node>`` at QoS 1, before the birth.
3. NBIRTH on ``spBv1.0/<group>/NBIRTH/<node>`` at QoS 0, ``seq`` 0: a timestamp, every metric with
   its name, alias (its 1-based place in the I/O table), datatype and value, ``bdSeq`` (the Will's
   value), and ``Node Control/Rebirth`` (Boolean false, no alias).
4. Every ``period_s`` (0.5 s), NDATA at QoS 0 with the metrics whose value changed, by alias,
   with ``seq`` one more than the last message's, wrapping 255 to 0. Nothing changed, nothing
   sent.
5. An NCMD whose ``Node Control/Rebirth`` is true republishes NBIRTH with the same ``bdSeq`` and
   ``seq`` back to 0. Any other NCMD metric is logged and ignored: this node never writes to the
   I/O table, so a host cannot override the PLC's outputs through it.
6. On shutdown, NDEATH at QoS 1 (waiting for its PUBACK), then DISCONNECT. A lost connection is
   retried every 2 s, each retry a new CONNECT with the next ``bdSeq``.

Datatypes: a BOOL tag is Boolean (11), a float32 tag is Float (9, a 32-bit float, wire type
fixed32), ``bdSeq`` is Int64 (4). Timestamps are milliseconds since the Unix epoch, UTC: the one
place OpenFactory reads the wall clock for a protocol, as Sparkplug requires; the simulation
itself never reads it.

Encoding: the Sparkplug B payload is a protocol buffer. Its field numbers (Payload: timestamp 1,
metrics 2, seq 3; Metric: name 1, alias 2, datatype 4, long_value 11, float_value 12,
boolean_value 14) are encoded here from the public protobuf wire format, so OpenFactory carries no
copy of ``sparkplug_b.proto`` (EPL-2.0) and needs no protobuf library. The MQTT client is a
minimal MQTT 3.1.1 client written from the OASIS standard (CONNECT, PUBLISH QoS 0 and the one
QoS 1 NDEATH, SUBSCRIBE, PINGREQ, DISCONNECT), so the publisher needs no extra package either.

The broker is a named endpoint in ``openfactory.toml`` (``[sparkplug]``, default
``127.0.0.1:1883``), never in a scene. Training and demo use only; not a safety-rated system.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

NAMESPACE = "spBv1.0"
REBIRTH = "Node Control/Rebirth"
#: Sparkplug B DataType enum values (sparkplug_b.proto, ``enum DataType``).
INT64, FLOAT, BOOLEAN = 4, 9, 11
KEEPALIVE_S = 30
RETRY_S = 2.0

# --- protobuf wire format ----------------------------------------------------------------------

VARINT, FIXED64, LEN, FIXED32 = 0, 1, 2, 5


def _varint(n: int) -> bytes:
    if n < 0:
        n &= (1 << 64) - 1  # protobuf encodes a negative int64 as its 64-bit two's complement
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint(field << 3 | wire)


def _len(field: int, data: bytes) -> bytes:
    return _key(field, LEN) + _varint(len(data)) + data


def encode_metric(
    *,
    name: str | None = None,
    alias: int | None = None,
    datatype: int,
    value: bool | float | int,
) -> bytes:
    """One ``Payload.Metric``: name (1), alias (2), datatype (4), then the value in the field its
    datatype uses: long_value (11) for Int64, float_value (12) for Float, boolean_value (14)."""
    out = b""
    if name is not None:
        out += _len(1, name.encode("utf-8"))
    if alias is not None:
        out += _key(2, VARINT) + _varint(alias)
    out += _key(4, VARINT) + _varint(datatype)
    if datatype == BOOLEAN:
        out += _key(14, VARINT) + _varint(1 if value else 0)
    elif datatype == FLOAT:
        out += _key(12, FIXED32) + struct.pack("<f", float(value))
    elif datatype == INT64:
        out += _key(11, VARINT) + _varint(int(value))
    else:
        raise ValueError(f"datatype {datatype} is not one this publisher sends")
    return out


def encode_payload(timestamp_ms: int, metrics: list[bytes], seq: int | None) -> bytes:
    """A ``Payload``: timestamp (1), each metric (2), and seq (3) unless None (NDEATH has none)."""
    out = _key(1, VARINT) + _varint(timestamp_ms)
    for metric in metrics:
        out += _len(2, metric)
    if seq is not None:
        out += _key(3, VARINT) + _varint(seq)
    return out


def _fields(data: bytes) -> list[tuple[int, int, Any]]:
    """(field, wire type, value) for each field of one protobuf message; LEN values are bytes."""
    out, i = [], 0
    while i < len(data):
        key, i = _read_varint(data, i)
        field, wire = key >> 3, key & 7
        if wire == VARINT:
            value, i = _read_varint(data, i)
        elif wire == FIXED32:
            value, i = data[i : i + 4], i + 4
        elif wire == FIXED64:
            value, i = data[i : i + 8], i + 8
        elif wire == LEN:
            size, i = _read_varint(data, i)
            value, i = data[i : i + size], i + size
        else:
            raise ValueError(f"protobuf wire type {wire} is not supported")
        out.append((field, wire, value))
    return out


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        byte = data[i]
        i += 1
        n |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return n, i


def decode_payload(data: bytes) -> dict[str, Any]:
    """The parts of a Payload this node reads from an NCMD: timestamp, seq and each metric's
    name, alias, datatype and value (Boolean, Int64 or Float)."""
    out: dict[str, Any] = {"metrics": []}
    for field, _wire, value in _fields(data):
        if field == 1:
            out["timestamp"] = value
        elif field == 3:
            out["seq"] = value
        elif field == 2:
            metric: dict[str, Any] = {}
            for f, _w, v in _fields(value):
                if f == 1:
                    metric["name"] = v.decode("utf-8")
                elif f == 2:
                    metric["alias"] = v
                elif f == 4:
                    metric["datatype"] = v
                elif f == 14:
                    metric["value"] = bool(v)
                elif f == 11:
                    metric["value"] = v
                elif f == 12:
                    metric["value"] = struct.unpack("<f", v)[0]
            out["metrics"].append(metric)
    return out


# --- MQTT 3.1.1 (OASIS mqtt-v3.1.1, section 2 and 3) --------------------------------------------

CONNECT, CONNACK, PUBLISH, PUBACK = 1, 2, 3, 4
SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 8, 9, 12, 13, 14


def _remaining(n: int) -> bytes:
    """The Remaining Length field (§2.2.3): 7 bits a byte, low first, high bit continues."""
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _utf8(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def _packet(kind: int, flags: int, body: bytes) -> bytes:
    return bytes([kind << 4 | flags]) + _remaining(len(body)) + body


def connect_packet(client_id: str, keepalive: int, will_topic: str, will_payload: bytes) -> bytes:
    """CONNECT (§3.1): protocol "MQTT" level 4, flags clean session + Will at QoS 1, not retained
    (0x0E), keep-alive, then the client id, Will topic and Will message."""
    flags = 0x02 | 0x04 | (1 << 3)  # clean session, Will flag, Will QoS 1, Will retain 0
    header = _utf8("MQTT") + bytes([4, flags]) + struct.pack(">H", keepalive)
    body = header + _utf8(client_id) + _utf8(will_topic)
    body += struct.pack(">H", len(will_payload)) + will_payload
    return _packet(CONNECT, 0, body)


def publish_packet(topic: str, payload: bytes, qos: int = 0, packet_id: int = 0) -> bytes:
    """PUBLISH (§3.3), never retained; a packet id only at QoS 1."""
    body = _utf8(topic) + (struct.pack(">H", packet_id) if qos else b"") + payload
    return _packet(PUBLISH, qos << 1, body)


def subscribe_packet(packet_id: int, topic: str, qos: int = 1) -> bytes:
    """SUBSCRIBE (§3.8): fixed header flags 0010, one topic filter with its requested QoS."""
    return _packet(SUBSCRIBE, 0x02, struct.pack(">H", packet_id) + _utf8(topic) + bytes([qos]))


class MqttClient:
    """The few MQTT 3.1.1 exchanges an edge node needs, on one asyncio connection."""

    def __init__(self, on_publish: Callable[[str, bytes], None]) -> None:
        self.on_publish = on_publish
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.next_id = 0
        self.waiting: dict[tuple[int, int], asyncio.Future[None]] = {}
        self.tasks: list[asyncio.Task[None]] = []
        self.closed = asyncio.Event()

    def _id(self) -> int:
        self.next_id = self.next_id % 0xFFFF + 1
        return self.next_id

    async def connect(
        self, host: str, port: int, client_id: str, will_topic: str, will_payload: bytes
    ) -> None:
        self.reader, self.writer = await asyncio.open_connection(host, port)
        self.writer.write(connect_packet(client_id, KEEPALIVE_S, will_topic, will_payload))
        await self.writer.drain()
        first, body = await asyncio.wait_for(self._read_packet(), 10)
        kind = first >> 4  # the packet type is the first byte's high nibble (§2.2.1)
        if kind != CONNACK or len(body) < 2 or body[1] != 0:
            code = body[1] if kind == CONNACK and len(body) > 1 else None
            raise ConnectionError(f"MQTT broker refused the connection (CONNACK {code})")
        self.tasks = [asyncio.create_task(self._read_loop()), asyncio.create_task(self._ping())]

    async def _read_packet(self) -> tuple[int, bytes]:
        assert self.reader is not None
        first = (await self.reader.readexactly(1))[0]
        n = shift = 0
        while True:
            byte = (await self.reader.readexactly(1))[0]
            n |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        return first, await self.reader.readexactly(n)

    async def _read_loop(self) -> None:
        try:
            while True:
                first, body = await self._read_packet()
                kind = first >> 4
                if kind == PUBLISH:
                    qos = (first >> 1) & 3
                    size = struct.unpack(">H", body[:2])[0]
                    topic, rest = body[2 : 2 + size].decode("utf-8"), body[2 + size :]
                    if qos:
                        packet_id, rest = struct.unpack(">H", rest[:2])[0], rest[2:]
                        self._send(_packet(PUBACK, 0, struct.pack(">H", packet_id)))
                    self.on_publish(topic, rest)
                elif kind in (PUBACK, SUBACK):
                    packet_id = struct.unpack(">H", body[:2])[0]
                    future = self.waiting.pop((kind, packet_id), None)
                    if future is not None and not future.done():
                        future.set_result(None)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self.closed.set()

    async def _ping(self) -> None:
        while not self.closed.is_set():
            await asyncio.sleep(KEEPALIVE_S / 2)
            self._send(_packet(PINGREQ, 0, b""))

    def _send(self, data: bytes) -> None:
        if self.writer is not None and not self.writer.is_closing():
            self.writer.write(data)

    async def _acked(self, kind: int, packet_id: int, data: bytes, timeout: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.waiting[(kind, packet_id)] = future
        self._send(data)
        await asyncio.wait_for(future, timeout)

    async def subscribe(self, topic: str, timeout: float = 10.0) -> None:
        packet_id = self._id()
        await self._acked(SUBACK, packet_id, subscribe_packet(packet_id, topic), timeout)

    def publish(self, topic: str, payload: bytes) -> None:
        """QoS 0, not retained."""
        self._send(publish_packet(topic, payload))

    async def publish_qos1(self, topic: str, payload: bytes, timeout: float = 5.0) -> None:
        packet_id = self._id()
        await self._acked(PUBACK, packet_id, publish_packet(topic, payload, 1, packet_id), timeout)

    async def close(self, *, clean: bool) -> None:
        """DISCONNECT first when ``clean`` (the broker then drops the Will), then the socket."""
        if clean:
            self._send(_packet(DISCONNECT, 0, b""))
        for task in self.tasks:
            task.cancel()
        if self.writer is not None:
            with contextlib.suppress(Exception):
                await self.writer.drain()
            self.writer.close()
            with contextlib.suppress(Exception):
                await self.writer.wait_closed()


# --- the edge node ------------------------------------------------------------------------------


@dataclass(frozen=True)
class SparkplugEndpoint:
    host: str = "127.0.0.1"
    port: int = 1883
    group: str = "OpenFactory"


def _valid_id(text: str, what: str) -> str:
    if not text or any(c in text for c in "+#/"):
        raise ValueError(f"Sparkplug {what} {text!r}: needs a name without '+', '#' or '/'")
    return text


class SparkplugPublisher:
    """One edge node publishing ``io`` (an :class:`~openfactory.core.iotable.IOTable`)."""

    def __init__(
        self,
        io: Any,
        endpoint: SparkplugEndpoint,
        node: str,
        *,
        period_s: float = 0.5,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.io = io
        self.endpoint = endpoint
        self.group = _valid_id(endpoint.group, "group id")
        self.node = _valid_id(node, "edge node id")
        self.period_s = period_s
        self.clock = clock
        self.tags = list(io.tags())
        self.alias = {t.name: i + 1 for i, t in enumerate(self.tags)}
        self.bdseq = -1  # the first CONNECT makes it 0
        self.seq = 0
        self.last: dict[str, Any] = {}
        self.client: MqttClient | None = None
        self.rebirths = 0
        self._rebirth = asyncio.Event()
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self.connected = False

    def topic(self, kind: str) -> str:
        return f"{NAMESPACE}/{self.group}/{kind}/{self.node}"

    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    def _datatype(self, tag: Any) -> int:
        return BOOLEAN if tag.dtype == "bool" else FLOAT

    def birth_payload(self) -> bytes:
        metrics = [
            encode_metric(name="bdSeq", datatype=INT64, value=self.bdseq),
            encode_metric(name=REBIRTH, datatype=BOOLEAN, value=False),
        ]
        self.last = {}
        for tag in self.tags:
            value = self.io.get(tag.name)
            self.last[tag.name] = value
            metrics.append(
                encode_metric(
                    name=tag.name,
                    alias=self.alias[tag.name],
                    datatype=self._datatype(tag),
                    value=value,
                )
            )
        self.seq = 0
        return encode_payload(self._now_ms(), metrics, 0)

    def death_payload(self) -> bytes:
        return encode_payload(
            self._now_ms(), [encode_metric(name="bdSeq", datatype=INT64, value=self.bdseq)], None
        )

    def data_payload(self) -> bytes | None:
        """NDATA of the metrics that changed since the last birth or data, or None."""
        metrics = []
        for tag in self.tags:
            value = self.io.get(tag.name)
            if value != self.last.get(tag.name):
                self.last[tag.name] = value
                metrics.append(
                    encode_metric(
                        alias=self.alias[tag.name], datatype=self._datatype(tag), value=value
                    )
                )
        if not metrics:
            return None
        self.seq = (self.seq + 1) % 256
        return encode_payload(self._now_ms(), metrics, self.seq)

    def _on_publish(self, topic: str, payload: bytes) -> None:
        if topic != self.topic("NCMD"):
            return
        try:
            command = decode_payload(payload)
        except (ValueError, IndexError, UnicodeDecodeError, struct.error):
            log.warning("sparkplug: an NCMD that is not a Sparkplug B payload, ignored")
            return
        for metric in command["metrics"]:
            if metric.get("name") == REBIRTH and metric.get("value") is True:
                self._rebirth.set()
            else:
                log.warning(
                    "sparkplug: NCMD %s ignored: this edge node is read-only telemetry",
                    metric.get("name", metric.get("alias")),
                )

    async def _session(self) -> None:
        self.bdseq = (self.bdseq + 1) % 256
        client = MqttClient(self._on_publish)
        await client.connect(
            self.endpoint.host,
            self.endpoint.port,
            f"openfactory-{self.group}-{self.node}"[:23],
            self.topic("NDEATH"),
            self.death_payload(),
        )
        self.client = client
        await client.subscribe(self.topic("NCMD"))
        client.publish(self.topic("NBIRTH"), self.birth_payload())
        self.connected = True
        self._ready.set()
        log.info(
            "sparkplug: edge node %s/%s born on %s:%d (bdSeq %d)",
            self.group,
            self.node,
            self.endpoint.host,
            self.endpoint.port,
            self.bdseq,
        )
        while not self._stop.is_set() and not client.closed.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._rebirth.wait(), self.period_s)
            if self._rebirth.is_set():
                self._rebirth.clear()
                self.rebirths += 1
                client.publish(self.topic("NBIRTH"), self.birth_payload())
                continue
            payload = self.data_payload()
            if payload is not None:
                client.publish(self.topic("NDATA"), payload)
        self.connected = False

    async def serve_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self._session()
            except (TimeoutError, OSError, ConnectionError) as exc:
                log.warning(
                    "sparkplug: broker %s:%d: %s; retrying in %g s",
                    self.endpoint.host,
                    self.endpoint.port,
                    exc,
                    RETRY_S,
                )
            finally:
                self._ready.set()  # a first attempt made, connected or not: the run goes on
            if not self._stop.is_set():
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), RETRY_S)

    async def wait_ready(self, timeout: float = 10.0) -> None:
        """Until the first connection attempt has ended. A broker that is down does not stop the
        run: the node keeps retrying and is born when the broker comes up."""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout)

    async def shutdown(self) -> None:
        """NDEATH at QoS 1 (waiting for its PUBACK), then DISCONNECT."""
        self._stop.set()
        client = self.client
        if client is None or client.closed.is_set():
            return
        with contextlib.suppress(asyncio.TimeoutError, OSError, ConnectionError):
            await client.publish_qos1(self.topic("NDEATH"), self.death_payload())
        await client.close(clean=True)
