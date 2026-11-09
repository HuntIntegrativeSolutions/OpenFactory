"""A small MQTT 3.1.1 broker for the Sparkplug tests (PLAN 3.5), written from the OASIS standard
(mqtt-v3.1.1) separately from ``openfactory.protocols.sparkplug``: it imports nothing from it and
parses every packet itself, so a misreading of the standard in the publisher does not pass by
being misread the same way here. It records every packet it receives, raw.

It does what the tests need and no more: CONNECT (with a Will) and CONNACK, SUBSCRIBE and SUBACK
(exact topic filters and a trailing ``#``), PUBLISH at QoS 0 and 1 with PUBACK, delivery to
matching subscribers at the QoS the publisher sent, PINGREQ and PINGRESP, DISCONNECT, and the Will
published when a client's connection ends without a DISCONNECT (§3.1.2.5). No retained messages,
no sessions, no QoS 2. It is a test fixture, not a broker to run anything with.
"""

from __future__ import annotations

import asyncio
import struct
import threading
from dataclasses import dataclass, field


@dataclass
class Received:
    """One packet a client sent: its type and flags (the first byte) and its body, raw."""

    client: str
    kind: int
    flags: int
    body: bytes


@dataclass
class Client:
    client_id: str = ""
    writer: asyncio.StreamWriter | None = None
    will: tuple[str, bytes, int] | None = None
    subscriptions: list[str] = field(default_factory=list)
    disconnected: bool = False


def _matches(pattern: str, topic: str) -> bool:
    if pattern.endswith("#"):
        return topic.startswith(pattern[:-1])
    return pattern == topic


def _string(data: bytes, i: int) -> tuple[str, int]:
    (size,) = struct.unpack(">H", data[i : i + 2])
    return data[i + 2 : i + 2 + size].decode("utf-8"), i + 2 + size


def _frame(first: int, body: bytes) -> bytes:
    out, n = bytearray([first]), len(body)
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out) + body


class TestBroker:
    """Runs on its own thread and event loop; ``port`` is set once it listens."""

    __test__ = False  # not a pytest test class

    def __init__(self) -> None:
        self.port = 0
        self.received: list[Received] = []
        #: (topic, payload, qos) of every PUBLISH a client sent, and of every Will published.
        self.published: list[tuple[str, bytes, int]] = []
        self.wills: list[tuple[str, bytes, int]] = []
        self.clients: list[Client] = []
        self.lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.server: asyncio.Server | None = None

    def start(self) -> TestBroker:
        self.thread.start()
        assert self.ready.wait(10), "test broker did not start"
        return self

    def stop(self) -> None:
        async def close() -> None:
            if self.server is not None:
                self.server.close()
            for c in self.clients:
                if c.writer is not None:
                    c.writer.close()

        asyncio.run_coroutine_threadsafe(close(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)

        async def listen() -> None:
            self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
            self.port = self.server.sockets[0].getsockname()[1]
            self.ready.set()

        self.loop.run_until_complete(listen())
        self.loop.run_forever()

    # -- what a test does --------------------------------------------------------------------------

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> None:
        """Deliver a message to every matching subscriber, as if another client published it."""
        asyncio.run_coroutine_threadsafe(self._deliver(topic, payload, qos), self.loop).result(5)

    def drop(self) -> None:
        """Cut every client's connection without a DISCONNECT from it (their Wills go out)."""

        async def cut() -> None:
            for c in list(self.clients):
                if c.writer is not None:
                    c.writer.transport.abort()

        asyncio.run_coroutine_threadsafe(cut(), self.loop).result(5)

    def messages(self, topic_part: str) -> list[tuple[str, bytes, int]]:
        with self.lock:
            return [m for m in self.published if topic_part in m[0]]

    # -- the protocol ------------------------------------------------------------------------------

    async def _deliver(self, topic: str, payload: bytes, qos: int) -> None:
        packet_id = 1
        for c in list(self.clients):
            if c.writer is None or not any(_matches(s, topic) for s in c.subscriptions):
                continue
            name = struct.pack(">H", len(topic.encode())) + topic.encode()
            body = name + (struct.pack(">H", packet_id) if qos else b"") + payload
            c.writer.write(_frame(0x30 | qos << 1, body))
            await c.writer.drain()

    async def _read(self, reader: asyncio.StreamReader) -> tuple[int, bytes]:
        first = (await reader.readexactly(1))[0]
        n = shift = 0
        while True:
            byte = (await reader.readexactly(1))[0]
            n |= (byte & 0x7F) << shift
            shift += 7
            if byte < 0x80:
                break
        return first, await reader.readexactly(n)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = Client(writer=writer)
        self.clients.append(client)
        try:
            while True:
                first, body = await self._read(reader)
                kind, flags = first >> 4, first & 0x0F
                with self.lock:
                    self.received.append(Received(client.client_id, kind, flags, body))
                if kind == 1:  # CONNECT (§3.1)
                    protocol, i = _string(body, 0)
                    level, connect_flags = body[i], body[i + 1]
                    assert (protocol, level) == ("MQTT", 4), (protocol, level)
                    i += 4  # level, flags, keep-alive
                    client.client_id, i = _string(body, i)
                    if connect_flags & 0x04:  # Will flag
                        will_topic, i = _string(body, i)
                        (size,) = struct.unpack(">H", body[i : i + 2])
                        will_qos = (connect_flags >> 3) & 3
                        client.will = (will_topic, body[i + 2 : i + 2 + size], will_qos)
                    with self.lock:
                        self.received[-1].client = client.client_id
                    writer.write(bytes([0x20, 2, 0, 0]))  # CONNACK, accepted
                elif kind == 8:  # SUBSCRIBE (§3.8): packet id, then (filter, qos) pairs
                    packet_id, i = body[:2], 2
                    granted = b""
                    while i < len(body):
                        topic, i = _string(body, i)
                        client.subscriptions.append(topic)
                        granted += bytes([min(body[i], 1)])
                        i += 1
                    writer.write(_frame(0x90, packet_id + granted))  # SUBACK
                elif kind == 3:  # PUBLISH (§3.3)
                    qos = (flags >> 1) & 3
                    topic, i = _string(body, 0)
                    if qos:
                        writer.write(_frame(0x40, body[i : i + 2]))  # PUBACK
                        i += 2
                    with self.lock:
                        self.published.append((topic, body[i:], qos))
                    await self._deliver(topic, body[i:], qos)
                elif kind == 12:  # PINGREQ
                    writer.write(bytes([0xD0, 0]))
                elif kind == 14:  # DISCONNECT: the Will is discarded (§3.14.4)
                    client.disconnected = True
                    break
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self.clients.remove(client)
            if client.will and not client.disconnected:  # §3.1.2.5: the Will goes out
                with self.lock:
                    self.wills.append(client.will)
                    self.published.append(client.will)
                await self._deliver(*client.will)
            writer.close()
