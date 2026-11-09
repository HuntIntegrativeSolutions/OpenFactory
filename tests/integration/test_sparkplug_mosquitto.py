"""PLAN 3.5, the outside check (ruled 2026-11-09): the Sparkplug B edge node against Mosquitto, a
broker OpenFactory did not write, with paho-mqtt (the dev group, used under its BSD-3-Clause
option) as a client OpenFactory did not write either.

- Our client publishes through Mosquitto; paho receives, and each payload is decoded both with
  OpenFactory's decoder and with the independent field parser of the hand-written broker test.
- paho publishes Sparkplug payloads; OpenFactory's client receives and decodes them: a rebirth
  request the node acts on, and a payload a bare client decodes.

CI installs Mosquitto with apt-get (.github/workflows/ci.yml); each test starts its own broker on
a free loopback port. On a machine without Mosquitto the tests skip (and fail under CI=true).
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
from test_sparkplug_broker import payload, table  # the independent parser and a small I/O table

from openfactory.core.iotable import Writer
from openfactory.protocols import sparkplug as sp

mqtt = pytest.importorskip("paho.mqtt.client")

GROUP, NODE = "Lab", "tank-level-pid"
TOPIC = f"spBv1.0/{GROUP}/{{}}/{NODE}"


def mosquitto_binary() -> str | None:
    return shutil.which("mosquitto") or next(
        (p for p in ("/usr/sbin/mosquitto", "/usr/local/sbin/mosquitto") if Path(p).is_file()),
        None,
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def broker(prerequisite, tmp_path):
    binary = mosquitto_binary()
    prerequisite(binary is not None, "mosquitto not installed (CI installs it with apt-get)")
    port = free_port()
    conf = tmp_path / "mosquitto.conf"
    conf.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\n", encoding="utf-8")
    proc = subprocess.Popen(
        [binary, "-c", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    deadline = time.monotonic() + 10
    while True:  # a hang guard while the broker starts listening, not a speed check
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            assert time.monotonic() < deadline, "mosquitto did not start listening"
            time.sleep(0.05)
    yield port
    proc.terminate()
    proc.wait(10)


class Listener:
    """A paho client subscribed to the group's Sparkplug topics, keeping every message."""

    def __init__(self, port: int) -> None:
        self.messages: list[tuple[str, bytes, int, bool]] = []
        self.lock = threading.Lock()
        self.subscribed = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="paho-listener")
        self.client.on_message = self._on_message
        self.client.on_subscribe = lambda *args: self.subscribed.set()
        self.client.connect("127.0.0.1", port)
        self.client.subscribe(f"spBv1.0/{GROUP}/#", qos=1)
        self.client.loop_start()
        assert self.subscribed.wait(10)

    def _on_message(self, client, userdata, msg) -> None:  # noqa: ANN001
        with self.lock:
            self.messages.append((msg.topic, bytes(msg.payload), msg.qos, msg.retain))

    def of(self, kind: str) -> list[tuple[str, bytes, int, bool]]:
        with self.lock:
            return [m for m in self.messages if m[0] == TOPIC.format(kind)]

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


async def until(condition, timeout: float = 10.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        await asyncio.sleep(0.02)


def test_our_node_through_mosquitto_decoded_by_paho_and_both_parsers(broker, monkeypatch):
    monkeypatch.setattr(sp, "RETRY_S", 0.2)
    listener = Listener(broker)
    io = table()
    node = sp.SparkplugPublisher(
        io, sp.SparkplugEndpoint("127.0.0.1", broker, GROUP), NODE, period_s=0.05
    )

    async def scenario() -> None:
        task = asyncio.create_task(node.serve_forever())
        await until(lambda: listener.of("NBIRTH"))
        _topic, data, _qos, retain = listener.of("NBIRTH")[0]
        assert retain is False  # Sparkplug: never retained
        ours, theirs = sp.decode_payload(data), payload(data)
        assert ours["seq"] == theirs["seq"] == 0
        assert theirs["metrics"]["TK_01_LT_AI"] == (sp.FLOAT, 1.0)
        assert theirs["metrics"]["bdSeq"] == (sp.INT64, 0)
        assert {m["name"] for m in ours["metrics"]} >= {"TK_01_LSH_DI", "TK_01_LT_AI", "bdSeq"}

        io.set("TK_01_LT_AI", 1.25, Writer.SIM)
        await until(lambda: listener.of("NDATA"))
        data = listener.of("NDATA")[0][1]
        assert payload(data)["metrics"] == {2: (sp.FLOAT, 1.25)}
        assert sp.decode_payload(data)["metrics"] == [
            {"alias": 2, "datatype": sp.FLOAT, "value": 1.25}
        ]

        # the link dies without a DISCONNECT: Mosquitto publishes the Will, the node comes back
        node.client.writer.transport.abort()  # type: ignore[union-attr]
        await until(lambda: listener.of("NDEATH") and len(listener.of("NBIRTH")) == 2)
        will = listener.of("NDEATH")[0]
        assert payload(will[1])["metrics"] == {"bdSeq": (sp.INT64, 0)}
        assert payload(listener.of("NBIRTH")[1][1])["metrics"]["bdSeq"] == (sp.INT64, 1)

        await node.shutdown()
        await asyncio.wait_for(task, 5)
        await until(lambda: len(listener.of("NDEATH")) == 2)  # the clean stop's NDEATH
        assert payload(listener.of("NDEATH")[1][1])["metrics"] == {"bdSeq": (sp.INT64, 1)}

    try:
        asyncio.run(scenario())
    finally:
        listener.close()


def test_paho_publishes_and_our_client_decodes(broker, monkeypatch):
    monkeypatch.setattr(sp, "RETRY_S", 0.2)
    listener = Listener(broker)
    sender = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="paho-sender")
    sender.connect("127.0.0.1", broker)
    sender.loop_start()
    node = sp.SparkplugPublisher(
        table(), sp.SparkplugEndpoint("127.0.0.1", broker, GROUP), NODE, period_s=0.05
    )
    # Payload{timestamp 1, metrics [{name "Node Control/Rebirth", datatype Boolean, true}]}, by hand
    rebirth = bytes.fromhex("0801") + b"\x12" + bytes([26])
    rebirth += b"\x0a\x14Node Control/Rebirth" + bytes.fromhex("200b7001")
    # Payload{timestamp 2, seq 7, metrics [{alias 2, Float 0.5}, {name "x", Int64 300}]}
    sample = bytes.fromhex("0802")
    sample += b"\x12\x09" + bytes.fromhex("1002" + "2009" + "650000003f")
    sample += b"\x12\x08" + bytes.fromhex("0a0178" + "2004" + "58ac02")
    sample += bytes.fromhex("1807")
    received: list[bytes] = []

    async def scenario() -> None:
        task = asyncio.create_task(node.serve_forever())
        await until(lambda: listener.of("NBIRTH"))
        sender.publish(TOPIC.format("NCMD"), rebirth, qos=1).wait_for_publish(5)
        await until(lambda: len(listener.of("NBIRTH")) == 2)
        assert node.rebirths == 1

        bare = sp.MqttClient(lambda topic, data: received.append(data))
        await bare.connect("127.0.0.1", broker, "ofbare", "spBv1.0/Lab/NDEATH/bare", b"")
        await bare.subscribe("lab/sample")
        sender.publish("lab/sample", sample, qos=1).wait_for_publish(5)
        await until(lambda: received)
        assert sp.decode_payload(received[0]) == {
            "timestamp": 2,
            "seq": 7,
            "metrics": [
                {"alias": 2, "datatype": sp.FLOAT, "value": 0.5},
                {"name": "x", "datatype": sp.INT64, "value": 300},
            ],
        }
        await bare.close(clean=True)
        await node.shutdown()
        await asyncio.wait_for(task, 5)

    try:
        asyncio.run(scenario())
    finally:
        sender.loop_stop()
        sender.disconnect()
        listener.close()
