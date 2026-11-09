"""PLAN 3.5: the Sparkplug B edge node against a local MQTT broker (tests/integration/
mqtt_test_broker.py, written separately from the publisher and run in-process, so CI and a
machine without Docker run it alike). What Ignition's MQTT Engine depends on: the session order
(CONNECT with the NDEATH Will, SUBSCRIBE to NCMD before NBIRTH), NBIRTH and NDATA sequence
numbers, a rebirth on request, the Will when the link drops, bdSeq + 1 on the next CONNECT, and
NDEATH at QoS 1 on a clean stop. Broker-side bytes are parsed by the broker's own code and the
payloads decoded field by field here, not by the publisher's decoder."""

from __future__ import annotations

import asyncio
import struct
import time

import pytest
from mqtt_test_broker import TestBroker  # beside this file (pytest puts its folder on sys.path)

from openfactory.core.iotable import IOTable, Tag, Writer
from openfactory.protocols import sparkplug as sp

GROUP, NODE = "OpenFactory", "tank-level-pid"
TOPIC = f"spBv1.0/{GROUP}/{{}}/{NODE}"


def fields(data: bytes) -> list[tuple[int, int, object]]:
    """Protobuf fields, parsed here independently: (field number, wire type, value)."""
    out, i = [], 0

    def varint() -> int:
        nonlocal i
        n = shift = 0
        while True:
            byte = data[i]
            i += 1
            n |= (byte & 0x7F) << shift
            shift += 7
            if byte < 0x80:
                return n

    while i < len(data):
        key = varint()
        number, wire = key >> 3, key & 7
        if wire == 0:
            value: object = varint()
        elif wire == 5:
            value, i = struct.unpack("<f", data[i : i + 4])[0], i + 4
        elif wire == 2:
            size = varint()
            value, i = data[i : i + size], i + size
        else:
            raise AssertionError(f"wire type {wire}")
        out.append((number, wire, value))
    return out


def payload(data: bytes) -> dict:
    """{timestamp, seq, metrics: {name or alias: (datatype, value)}} from a Payload."""
    out: dict = {"metrics": {}}
    for number, _wire, value in fields(data):
        if number == 1:
            out["timestamp"] = value
        elif number == 3:
            out["seq"] = value
        elif number == 2:
            m = {n: v for n, _w, v in fields(value)}  # type: ignore[arg-type]
            key = m[1].decode() if 1 in m else m[2]
            val = m.get(14, m.get(12, m.get(11)))
            out["metrics"][key] = (m[4], val)
    return out


def table() -> IOTable:
    io = IOTable()
    io.add(Tag.new("TK_01_LSH_DI", "DI"))
    io.add(Tag.new("TK_01_LT_AI", "AI", value=1.0))
    return io


async def until(condition, timeout: float = 10.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        await asyncio.sleep(0.02)


@pytest.fixture
def broker():
    b = TestBroker().start()
    yield b
    b.stop()


def test_an_edge_node_session_as_mqtt_engine_expects_it(broker, monkeypatch):
    monkeypatch.setattr(sp, "RETRY_S", 0.2)
    io = table()
    node = sp.SparkplugPublisher(
        io, sp.SparkplugEndpoint("127.0.0.1", broker.port, GROUP), NODE, period_s=0.05
    )

    async def scenario() -> None:
        task = asyncio.create_task(node.serve_forever())
        await node.wait_ready()
        await until(lambda: broker.messages("/NBIRTH/"))

        # 1. CONNECT: clean session, Will at QoS 1 not retained, on NDEATH with bdSeq 0
        connect = next(r for r in broker.received if r.kind == 1)
        assert connect.body[7] == 0x0E  # connect flags after "MQTT" and level 4
        will_topic, will_payload, will_qos = next(c for c in broker.clients).will  # type: ignore[misc]
        assert (will_topic, will_qos) == (TOPIC.format("NDEATH"), 1)
        death = payload(will_payload)
        assert death["metrics"] == {"bdSeq": (sp.INT64, 0)} and "seq" not in death

        # 2. SUBSCRIBE to NCMD at QoS 1 arrives before the NBIRTH publish
        kinds = [(r.kind, r.body) for r in broker.received]
        sub = next(i for i, (k, _b) in enumerate(kinds) if k == 8)
        birth_at = next(i for i, (k, b) in enumerate(kinds) if k == 3 and b"NBIRTH" in b)
        assert sub < birth_at
        assert kinds[sub][1].endswith(TOPIC.format("NCMD").encode() + b"\x01")

        # 3. NBIRTH at QoS 0, seq 0: every metric with its name, the bdSeq, the rebirth control
        topic, data, qos = broker.messages("/NBIRTH/")[0]
        birth = payload(data)
        assert (topic, qos, birth["seq"]) == (TOPIC.format("NBIRTH"), 0, 0)
        assert birth["metrics"] == {
            "bdSeq": (sp.INT64, 0),
            "Node Control/Rebirth": (sp.BOOLEAN, 0),
            "TK_01_LSH_DI": (sp.BOOLEAN, 0),
            "TK_01_LT_AI": (sp.FLOAT, 1.0),
        }

        # 4. a change goes out as NDATA by alias, seq 1
        io.set("TK_01_LT_AI", 1.25, Writer.SIM)
        await until(lambda: broker.messages("/NDATA/"))
        data = payload(broker.messages("/NDATA/")[0][1])
        assert data["seq"] == 1 and data["metrics"] == {2: (sp.FLOAT, 1.25)}

        # 5. a rebirth request: NBIRTH again, same bdSeq, seq back to 0
        rebirth = bytes.fromhex("0801") + b"\x12" + bytes([26])
        rebirth += b"\x0a\x14Node Control/Rebirth" + bytes.fromhex("200b7001")
        broker.publish(TOPIC.format("NCMD"), rebirth)
        await until(lambda: len(broker.messages("/NBIRTH/")) == 2)
        second = payload(broker.messages("/NBIRTH/")[1][1])
        assert second["seq"] == 0 and second["metrics"]["bdSeq"] == (sp.INT64, 0)
        # a command for anything else writes nothing: this node is read-only
        write = bytes.fromhex("0801") + b"\x12" + bytes([6]) + bytes.fromhex("1001200b7001")
        broker.publish(TOPIC.format("NCMD"), write)
        await asyncio.sleep(0.2)
        assert io.get("TK_01_LSH_DI") is False and node.rebirths == 1

        # 6. the link drops: the broker publishes the Will; the node comes back with bdSeq 1
        broker.drop()
        await until(lambda: broker.wills and len(broker.messages("/NBIRTH/")) == 3)
        assert payload(broker.wills[0][1])["metrics"] == {"bdSeq": (sp.INT64, 0)}
        third = payload(broker.messages("/NBIRTH/")[2][1])
        assert third["metrics"]["bdSeq"] == (sp.INT64, 1) and third["seq"] == 0

        # 7. a clean stop: NDEATH at QoS 1 with the current bdSeq, then DISCONNECT (no Will)
        await node.shutdown()
        await asyncio.wait_for(task, 5)
        topic, data, qos = broker.messages("/NDEATH/")[-1]
        assert (qos, payload(data)["metrics"]) == (1, {"bdSeq": (sp.INT64, 1)})
        await until(lambda: any(r.kind == 14 for r in broker.received))
        assert len(broker.wills) == 1  # the clean stop sent no second Will

    asyncio.run(scenario())


def test_a_broker_that_is_down_does_not_stop_the_run(monkeypatch):
    monkeypatch.setattr(sp, "RETRY_S", 0.05)
    node = sp.SparkplugPublisher(table(), sp.SparkplugEndpoint("127.0.0.1", 9, GROUP), NODE)

    async def scenario() -> None:
        task = asyncio.create_task(node.serve_forever())
        await asyncio.wait_for(node.wait_ready(), 5)  # returns: the run goes on without it
        assert not node.connected
        await node.shutdown()
        await asyncio.wait_for(task, 5)

    asyncio.run(scenario())


def test_run_sparkplug_births_the_scene_as_an_edge_node(broker, tmp_path):
    """`openfactory run --sparkplug` with the broker named in openfactory.toml [sparkplug]."""
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    scene = tmp_path / "tank-level-pid.yaml"  # `run` writes lockfiles next to the scene
    scene.write_text((repo / "scenes" / scene.name).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "openfactory.toml").write_text(
        f'[sparkplug]\nbroker = "127.0.0.1"\nport = {broker.port}\ngroup = "Lab"\n',
        encoding="utf-8",
    )
    cmd = [
        sys.executable, "-m", "openfactory", "run", str(scene), "--sparkplug", "--headless",
        "--duration", "3", "--port", "0", "-q",
    ]  # fmt: skip
    done = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    births = broker.messages("spBv1.0/Lab/NBIRTH/tank-level-pid")
    assert births, broker.published
    names = set(payload(births[0][1])["metrics"])
    assert {"TK_01_LT_AI", "FV_01_POS_AO", "TK_01_LSH_DI", "bdSeq"} <= names
    assert broker.messages("spBv1.0/Lab/NDATA/tank-level-pid")  # the level moved: NDATA
    deaths = broker.messages("spBv1.0/Lab/NDEATH/tank-level-pid")
    assert deaths and deaths[-1][2] == 1  # the clean stop's NDEATH, QoS 1
