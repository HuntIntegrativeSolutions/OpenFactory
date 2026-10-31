"""PLAN 2.4 Done-when, OPC UA half: an asyncua client reads a sensor and writes a belt run bit on
the sorter served by :class:`OpcUaServer`, a sensor is read-only, and after the server is
restarted under the connected client, the client reconnects (as Ignition's OPC UA client does)
and finds the same nodes, with the simulation running throughout."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from pathlib import Path

import pytest

asyncua = pytest.importorskip("asyncua")  # the opcua extra; CI installs --all-extras

from asyncua import Client, ua  # noqa: E402

from openfactory.core.config import Endpoint  # noqa: E402
from openfactory.core.loader import load  # noqa: E402
from openfactory.protocols.opcua import NAMESPACE, OpcUaServer  # noqa: E402
from openfactory.server.runtime import Runtime  # noqa: E402

SORTER = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_for(predicate, seconds: float, step: float = 0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if value := await predicate():
            return value
        await asyncio.sleep(step)
    raise AssertionError(f"not true within {seconds} s")


def test_client_reads_a_sensor_writes_a_run_bit_and_survives_a_server_restart():
    loaded = load(SORTER)
    runtime = Runtime(loaded)
    folders = {c.id: c.description for c in loaded.scene.components}
    server = OpcUaServer(
        loaded.io, Endpoint(port=free_port()), scene_name="sorting-by-height", folders=folders
    )
    report: list[str] = []

    async def session() -> None:
        task = asyncio.create_task(server.serve_forever())
        await server.wait_ready()
        client = Client(server.url, timeout=2)
        await client.connect()
        try:
            ns = await client.get_namespace_index(NAMESPACE)

            def node(tag: str):
                return client.get_node(ua.NodeId(tag, ns))

            # browse: scene folder > component folder > tag
            scene = await client.nodes.objects.get_child([f"{ns}:sorting-by-height"])
            pe = await scene.get_child([f"{ns}:PE_01", f"{ns}:PE_01_DI"])
            assert pe.nodeid == ua.NodeId("PE_01_DI", ns)
            beat = await scene.get_child([f"{ns}:SIM_HEARTBEAT_DI"])
            assert (await beat.read_data_type_as_variant_type()) == ua.VariantType.Boolean

            # write the belt run bit and the emitter enable; the I/O table gets them as the PLC
            for tag in ("CONV_01_RUN_DO", "CONV_02_RUN_DO", "EMT_01_EN_DO"):
                await node(tag).write_value(ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))
            await node("CONV_01_SPD_AO").write_value(
                ua.DataValue(ua.Variant(100.0, ua.VariantType.Float))
            )
            assert loaded.io.get("CONV_01_RUN_DO") is True
            assert loaded.io.get("CONV_01_SPD_AO") == pytest.approx(100.0)
            report.append("wrote CONV_01_RUN_DO, CONV_02_RUN_DO, EMT_01_EN_DO = True")

            # read sensors: the belt's running feedback, then a tall box at the photo eye
            async def running():
                return await node("CONV_01_RUN_DI").read_value()

            await wait_for(running, 2)
            start = time.monotonic()

            async def blocked():
                return await node("PE_01_DI").read_value()

            await wait_for(blocked, 20)
            report.append(
                f"read CONV_01_RUN_DI True; PE_01_DI True after {time.monotonic() - start:.1f} s "
                f"(tick {runtime.snapshot['tick']})"
            )

            # a sensor is read-only to clients
            with pytest.raises(ua.UaStatusCodeError, match="BadNotWritable|BadUserAccessDenied"):
                await node("PE_01_DI").write_value(
                    ua.DataValue(ua.Variant(False, ua.VariantType.Boolean))
                )

            # restart the server under the connected client
            tick_before = runtime.snapshot["tick"]
            await server.shutdown()
            await task
            with pytest.raises(Exception):  # noqa: B017 - any comms error; the server is gone
                await node("CONV_01_RUN_DI").read_value()
            down_at = time.monotonic()
            task = asyncio.create_task(server.serve_forever())

            async def reconnect():  # what an OPC UA client with reconnect does
                with contextlib.suppress(Exception):
                    await client.disconnect()
                try:
                    await client.connect()
                    return True
                except (TimeoutError, OSError, ua.UaError):
                    return False

            await wait_for(reconnect, 20, step=0.25)
            ns = await client.get_namespace_index(NAMESPACE)
            assert await node("CONV_01_RUN_DI").read_value() is True
            await node("CONV_01_RUN_DO").write_value(
                ua.DataValue(ua.Variant(False, ua.VariantType.Boolean))
            )
            assert loaded.io.get("CONV_01_RUN_DO") is False

            async def stopped():
                return not await node("CONV_01_RUN_DI").read_value()

            await wait_for(stopped, 2)
            ticked = runtime.snapshot["tick"] - tick_before
            report.append(
                f"restart: reconnected {time.monotonic() - down_at:.1f} s after the server went "
                f"down; same NodeIds; CONV_01_RUN_DI read True, CONV_01_RUN_DO written False, "
                f"CONV_01_RUN_DI then False; the sim ran {ticked} ticks meanwhile"
            )
            assert ticked > 0
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()
            await server.shutdown()
            await asyncio.wait_for(task, 10)

    runtime.start()
    try:
        asyncio.run(session())
    finally:
        runtime.stop()
    print("\n".join(report))


def test_run_opcua_serves_every_path_the_ignition_export_names(tmp_path):
    """`openfactory run --opcua` from the CLI, and every OPC item path in the sorter's Ignition
    export resolves on it to a node of the exported data type; the heartbeat changes."""
    import subprocess
    import sys

    from openfactory.core.schema import load_scene
    from openfactory.export.ignition_tags import ignition_tags

    scene = tmp_path / SORTER.name  # its own folder: run writes lockfiles beside the scene
    scene.write_text(SORTER.read_text(encoding="utf-8"), encoding="utf-8")
    script = SORTER.parents[1] / "examples" / "pyplc" / "sorting-by-height.py"
    doc = ignition_tags(load_scene(scene))
    paths: dict[str, str] = {}  # scene tag -> Ignition data type
    types = {
        t["name"]: {m["name"]: m["dataType"] for m in t["tags"]} for t in doc["tags"][0]["tags"]
    }
    for item in doc["tags"][1]["tags"]:
        if item["tagType"] == "AtomicTag":
            paths[item["opcItemPath"].split(";s=")[1]] = item["dataType"]
            continue
        for member, data_type in types[item["typeId"]].items():
            if tag := item["parameters"][f"Tag_{member}"]["value"]:
                paths[tag] = data_type
    port = free_port()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "openfactory", "run", str(scene), "--headless", "--opcua",
            "--opcua-port", str(port), "--port", str(free_port()), "--script", str(script),
            "--duration", "20", "-q",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
    variant = {"Boolean": ua.VariantType.Boolean, "Float4": ua.VariantType.Float}

    async def session() -> str:
        client = Client(f"opc.tcp://127.0.0.1:{port}/openfactory/", timeout=2)

        async def up():
            try:
                await client.connect()
                return True
            except (TimeoutError, OSError, ua.UaError):
                assert proc.poll() is None, proc.stdout.read()
                return False

        await wait_for(up, 20, step=0.25)
        try:
            ns = await client.get_namespace_index(NAMESPACE)
            for tag, data_type in paths.items():
                node = client.get_node(ua.NodeId(tag, ns))
                assert await node.read_data_type_as_variant_type() == variant[data_type], tag
            beat = client.get_node(ua.NodeId("SIM_HEARTBEAT_DI", ns))
            first = await beat.read_value()

            async def changed():
                return await beat.read_value() != first

            await wait_for(changed, 2)
            return f"run --opcua: {len(paths)} Ignition item paths resolved; heartbeat changed"
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()

    try:
        line = asyncio.run(session())
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()
    print(line)
