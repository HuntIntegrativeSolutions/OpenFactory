"""PLAN 5.4b: one address map for a plant, read from the Modbus server in three runs (the plant,
and each area alone with ``--area``)."""

from __future__ import annotations

import asyncio
import socket
import struct
from pathlib import Path

import pytest
import yaml
from pymodbus.client import AsyncModbusTcpClient
from typer.testing import CliRunner

from openfactory.cli import app as cli
from openfactory.core.config import Endpoint
from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import load_scene
from openfactory.protocols.modbus import (
    AddressError,
    ModbusServer,
    lockfile_path,
    scene_register_map,
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def belt(cid: str, x: float) -> dict:
    return {"id": cid, "type": "belt_conveyor", "description": f"Belt {cid}",
            "position": [x, 0, 0], "length": 2.0, "width": 0.5, "speed_mps": 0.3,
            "io": {"run": f"{cid}_RUN_DO", "running": f"{cid}_RUN_DI",
                   "speed": {"tag": f"{cid}_SPD_AO", "range": [0, 1]}}}  # fmt: skip


def eye(cid: str, x: float) -> dict:
    return {"id": cid, "type": "photo_eye", "description": f"Eye {cid}",
            "position": [x, 0.1, 0.3], "beam_length": 0.6,
            "io": {"detect": f"{cid}_DI"}}  # fmt: skip


def make_plant(tmp_path: Path) -> Path:
    folder = tmp_path / "plant"
    (folder / "areas").mkdir(parents=True)
    for name, comps in (("a", [belt("CV_101", 0.0), eye("PE_101", 1.8)]),
                        ("b", [belt("CV_201", 2.0), eye("PE_201", 3.8)])):  # fmt: skip
        doc = {"openfactory": "0.1", "scene": {"name": f"area-{name}", "description": "An area."},
               "plc": {"protocol": "modbus_tcp"}, "components": comps}  # fmt: skip
        (folder / "areas" / f"{name}.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    plant = {"openfactory": "0.1", "scene": {"name": "plant", "description": "A plant."},
             "plc": {"protocol": "modbus_tcp"},
             "include": [{"area": "A", "file": "areas/a.yaml"},
                         {"area": "B", "file": "areas/b.yaml"}],
             "connections": [{"from": "CV_101", "to": "CV_201"}]}  # fmt: skip
    path = folder / "plant.yaml"
    path.write_text(yaml.safe_dump(plant, sort_keys=False), encoding="utf-8")
    return path


def read_back(plant: Path, area: str | None, plant_map) -> dict[str, object]:
    """Serve one run's I/O table and read every plant tag at the plant's address."""
    scene = load_scene(plant, area=area)
    loaded = build(scene, base_dir=plant.parent)
    rmap = scene_register_map(scene, scene_path=plant)
    tags = [t.name for t in loaded.io.tags()]
    for i, name in enumerate(tags):  # distinct values: alternate bits, numbered floats
        kind = name.rsplit("_", 1)[-1]
        value = (i % 2 == 0) if kind in ("DI", "DO") else 0.1 * (i + 1)
        writer = Writer.PLC if kind in ("DO", "AO") else Writer.SIM
        loaded.io.set(name, value, writer)
    expected = {n: loaded.io.get(n) for n in tags}
    got: dict[str, object] = {}

    async def main() -> None:
        server = ModbusServer(loaded.io, "float32", Endpoint(bind="127.0.0.1", port=free_port()),
                              rmap)  # fmt: skip
        task = asyncio.create_task(server.serve_forever())
        try:
            await server.wait_ready()
            client = AsyncModbusTcpClient("127.0.0.1", port=server.endpoint.port)
            assert await client.connect()
            try:
                for a in plant_map.assignments:
                    read = {"DI": client.read_discrete_inputs, "DO": client.read_coils,
                            "AI": client.read_input_registers,
                            "AO": client.read_holding_registers}[a.kind]  # fmt: skip
                    reply = await read(a.address, count=1 if a.kind in ("DI", "DO") else 2)
                    if reply.isError():  # past the table's last address: ILLEGAL ADDRESS
                        got[a.tag] = "refused"
                    elif a.kind in ("DI", "DO"):
                        got[a.tag] = reply.bits[0]
                    else:
                        words = reply.registers
                        got[a.tag] = struct.unpack(">f", struct.pack(">HH", *words))[0]
            finally:
                client.close()
        finally:
            await server.shutdown()
            task.cancel()

    asyncio.run(main())
    for name in tags:
        assert rmap.by_tag[name].address == plant_map.by_tag[name].address, name
    return {"expected": expected, "got": got, "tags": tags}


def test_every_tag_has_the_same_address_in_the_plant_and_each_area_alone(tmp_path) -> None:
    plant = make_plant(tmp_path)
    plant_map = scene_register_map(load_scene(plant), scene_path=plant)  # writes the lockfile
    lock = lockfile_path(plant, "float32")
    before = lock.read_bytes()
    runs = {area: read_back(plant, area, plant_map) for area in (None, "A", "B")}
    assert sorted(runs[None]["tags"]) == sorted(a.tag for a in plant_map.assignments)
    holes: dict[str | None, set[str]] = {}
    for area, run in runs.items():
        for name in plant_map.by_tag:
            value = run["got"][name]
            if name in run["tags"]:
                assert value == pytest.approx(run["expected"][name], abs=1e-6), (area, name)
            else:  # another area's address: a hole, read as 0 or false, or past the end
                assert value in (False, 0.0, "refused"), (area, name)
                holes.setdefault(area, set()).add(name)
    assert None not in holes and holes["A"] == set(runs["B"]["tags"]) - {"SIM_HEARTBEAT_DI"}
    assert all(runs[None]["got"][n] != "refused" for n in plant_map.by_tag)
    a_only = set(runs["A"]["tags"]) - {"SIM_HEARTBEAT_DI"}
    assert a_only == {"CV_101_RUN_DO", "CV_101_RUN_DI", "CV_101_SPD_AO", "PE_101_DI"}
    assert lock.read_bytes() == before  # the area runs never wrote the plant's lockfile


def test_an_area_run_needs_the_plants_lockfile_and_never_adds_to_it(tmp_path) -> None:
    plant = make_plant(tmp_path)
    with pytest.raises(AddressError, match="does not exist yet: run, test or export the whole "
                                           "plant once"):  # fmt: skip
        scene_register_map(load_scene(plant, area="A"), scene_path=plant)
    scene_register_map(load_scene(plant), scene_path=plant)
    lock = lockfile_path(plant, "float32")
    lock.write_text(lock.read_text(encoding="utf-8").replace("  PE_101_DI:", "  PE_999_DI:"),
                    encoding="utf-8")  # fmt: skip
    before = lock.read_bytes()
    with pytest.raises(AddressError, match="PE_101_DI not in plant.float32.modbus.yaml"):
        scene_register_map(load_scene(plant, area="A"), scene_path=plant)
    assert lock.read_bytes() == before


def test_area_on_the_commands(tmp_path) -> None:
    plant = make_plant(tmp_path)
    runner = CliRunner()
    done = runner.invoke(cli, ["export", str(plant), "--format", "csv", "-o",
                               str(tmp_path / "plant.csv")])  # fmt: skip
    assert done.exit_code == 0, done.output
    lock = lockfile_path(plant, "float32").read_bytes()
    done = runner.invoke(cli, ["export", str(plant), "--area", "B", "--format", "csv", "-o",
                               str(tmp_path / "b.csv")])  # fmt: skip
    assert done.exit_code == 0, done.output
    rows = (tmp_path / "b.csv").read_text(encoding="utf-8")
    assert "CV_201_RUN_DO" in rows and "CV_101_RUN_DO" not in rows
    assert lockfile_path(plant, "float32").read_bytes() == lock
    assert not (plant.parent / "plant.CONTEXT.md").exists() or "CV_101" in (
        plant.parent / "plant.CONTEXT.md"
    ).read_text(encoding="utf-8")  # an area run never writes the plant's context
    done = runner.invoke(cli, ["run", str(plant), "--area", "C", "--as-fast-as-possible",
                               "--duration", "1"])  # fmt: skip
    assert done.exit_code == 1
    assert "no area 'C'; its areas are A, B" in done.output


def test_the_help_of_export_area_states_both_meanings_in_one_sentence_each() -> None:
    """Ruled 2026-11-18: on a plant file it selects the area; on any other scene it names the
    SCADA export's folder."""
    import typer

    export = typer.main.get_command(cli).commands["export"]
    text = next(p.help for p in export.params if p.name == "area")
    sentences = [s.strip() for s in text.split(". ") if s.strip()]
    assert len(sentences) == 2, sentences
    assert sentences[0].startswith("On a plant file") and "selects the area" in sentences[0]
    assert sentences[1].startswith("On any other scene") and "area folder" in sentences[1]


# --- the shipped sample plant (PLAN 5.4d) ---------------------------------------------------

SAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "plants" / "wash-plant"


def sample_copy(tmp_path: Path) -> Path:
    import shutil

    folder = tmp_path / "wash-plant"
    shutil.copytree(SAMPLE, folder, ignore=shutil.ignore_patterns("__pycache__", "*.CONTEXT.md"))
    (tmp_path / "_library").mkdir()
    shutil.copyfile(SAMPLE.parents[1] / "_library" / "driven_item.yaml",
                    tmp_path / "_library" / "driven_item.yaml")  # fmt: skip
    (tmp_path / "openfactory.toml").write_text("", encoding="utf-8")  # a project root
    return folder / "wash-plant.yaml"


def test_the_sample_plant_keeps_one_address_map_in_all_three_runs(tmp_path) -> None:
    plant = sample_copy(tmp_path)
    lock = lockfile_path(plant, "float32")
    before = lock.read_bytes()
    plant_map = scene_register_map(load_scene(plant), scene_path=plant, write=False)
    runs = {area: read_back(plant, area, plant_map) for area in (None, "LOAD", "WASH")}
    for area, run in runs.items():
        for name in plant_map.by_tag:
            if name in run["tags"]:
                assert run["got"][name] == pytest.approx(run["expected"][name], abs=1e-6)
            else:
                assert run["got"][name] in (False, 0.0, "refused"), (area, name)
    assert {n for n in runs["LOAD"]["tags"] if n.startswith("TK_")} == set()
    assert "TK_201_LVL_AI" in runs["WASH"]["tags"] and "CV_101_RUN_DO" not in runs["WASH"]["tags"]
    assert lock.read_bytes() == before == (SAMPLE / lock.name).read_bytes()


@pytest.mark.parametrize("seconds", [60, 300])
@pytest.mark.parametrize("area", [None, "LOAD", "WASH"])
def test_the_sample_plant_and_each_area_pass_their_checks(tmp_path, area, seconds) -> None:
    plant = sample_copy(tmp_path)
    args = ["test", str(plant), "--script", str(plant.with_suffix(".py")), "-q",
            "--duration", str(seconds)] + (["--area", area] if area else [])  # fmt: skip
    done = CliRunner().invoke(cli, args)
    assert done.exit_code == 0, done.output
    expected = {None: "PASS: 7 of 7", "LOAD": "PASS: 1 of 1", "WASH": "PASS: 4 of 4"}[area]
    assert expected in done.output
    if area is None:
        assert "carton: " in done.output and "reached REM_201, none anywhere else" in done.output
