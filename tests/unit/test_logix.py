"""PLAN 2.2: protocols/logix.py against a fake controller written here, a stand-in for pycomm3's
LogixDriver (open, close, tags, read, write) whose link can be dropped. Covers batching, BOOL and
REAL mapping, a missing and a mismatched tag, and resuming after a 10 s drop with no restart.

What the fake cannot prove: that pycomm3 talks to a real controller, Echo or Emulate at all
(the CIP path, the Forward Open, the tag upload), real timing and packet splitting, what a pulled
cable looks like to pycomm3 (a socket timeout after socket_timeout, 5 s by default, rather than
an immediate CommError), or how a controller answers a write to a tag with External Access Read
Only. Those wait for the bench run (PLAN 2.2's Done-when, ADR-0002)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

import pytest
from pycomm3.exceptions import CommError
from pycomm3.tag import Tag as PyTag

from openfactory.core.heartbeat import heartbeat_tag
from openfactory.core.iotable import IOTable, Tag, Writer
from openfactory.protocols.logix import LogixClient


def table() -> IOTable:
    return IOTable(
        [
            Tag.new("PE_01_DI", "DI"),
            Tag.new("LT_01_AI", "AI"),
            Tag.new("CONV_01_RUN_DO", "DO"),
            Tag.new("FV_01_POS_AO", "AO"),
            heartbeat_tag(),
        ]
    )


class Controller:
    """The fake controller's memory, its logic, and the link to it."""

    def __init__(self, types: dict[str, str], logic: Callable[[dict], None] | None = None):
        defaults = {"BOOL": False, "REAL": 0.0, "DINT": 0, "INT": 0}
        self.types = types
        self.values = {name: defaults[t] for name, t in types.items()}
        self.logic = logic
        self.up = True
        self.epoch = 0  # a dropped link ends every open session
        self.opens = 0
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def drop(self) -> None:
        self.up = False
        self.epoch += 1

    def restore(self) -> None:
        self.up = True


class FakeDriver:
    """pycomm3 LogixDriver's surface: open, close, tags, read(*names), write(*(name, value))."""

    def __init__(self, plc: Controller) -> None:
        self.plc = plc
        self.epoch: int | None = None

    @property
    def tags(self) -> dict:
        return {n: {"data_type_name": t, "tag_type": "atomic"} for n, t in self.plc.types.items()}

    def _alive(self) -> None:
        if not self.plc.up or self.epoch != self.plc.epoch:
            raise CommError("socket connection broken.")

    def open(self) -> bool:
        if not self.plc.up:
            raise CommError("Failed to open socket to 192.0.2.10:44818")
        self.plc.opens += 1
        self.epoch = self.plc.epoch
        return True

    def close(self) -> None:
        self.epoch = None

    def _result(self, results: list[PyTag]):
        return results[0] if len(results) == 1 else results

    def read(self, *names: str):
        self._alive()
        self.plc.calls.append(("read", names))
        if self.plc.logic:
            self.plc.logic(self.plc.values)
        out = []
        for n in names:
            if n not in self.plc.types:
                out.append(PyTag(n, None, None, f"Tag doesn't exist - {n}"))
            else:
                out.append(PyTag(n, self.plc.values[n], self.plc.types[n], None))
        return self._result(out)

    def write(self, *pairs: tuple[str, object]):
        self._alive()
        self.plc.calls.append(("write", tuple(n for n, _ in pairs)))
        out = []
        for n, v in pairs:
            if n not in self.plc.types:
                out.append(PyTag(n, None, None, f"Tag doesn't exist - {n}"))
                continue
            want = {"BOOL": bool, "REAL": float}[self.plc.types[n]]
            assert type(v) is want, (n, v)  # OpenFactory sends BOOL as bool, REAL as float
            self.plc.values[n] = v
            out.append(PyTag(n, v, self.plc.types[n], None))
        return self._result(out)


TYPES = {
    "PE_01_DI": "BOOL",
    "LT_01_AI": "REAL",
    "CONV_01_RUN_DO": "BOOL",
    "FV_01_POS_AO": "REAL",
    "SIM_HEARTBEAT_DI": "BOOL",
}


def logic(
    v: dict,
) -> None:  # the controller's program: run while the eye is clear, valve = 2 x level
    v["CONV_01_RUN_DO"] = not v["PE_01_DI"]
    v["FV_01_POS_AO"] = 2.0 * v["LT_01_AI"]


async def until(check: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout  # a hang guard, not a speed check
    while not check():
        assert time.monotonic() < deadline, "condition not met in time"
        await asyncio.sleep(0.01)


def client(io: IOTable, plc: Controller, **kw) -> LogixClient:
    kw.setdefault("period_s", 0.01)
    kw.setdefault("backoff_s", (0.05, 0.1, 0.2))
    return LogixClient(io, "192.0.2.10", driver_factory=lambda path: FakeDriver(plc), **kw)


def run(coro) -> None:  # noqa: ANN001
    asyncio.run(asyncio.wait_for(coro, 60))


def test_one_write_and_one_read_per_cycle_only_changed_tags_written_bool_and_real_mapped():
    async def main():
        io, plc = table(), Controller(dict(TYPES), logic)
        c = client(io, plc)
        task = asyncio.create_task(c.serve_forever())
        await until(lambda: c.state.cycles >= 3)
        first_write = next(names for kind, names in plc.calls if kind == "write")
        assert set(first_write) == {"PE_01_DI", "LT_01_AI", "SIM_HEARTBEAT_DI"}  # all, once
        reads = [names for kind, names in plc.calls if kind == "read"]
        assert all(set(r) == {"CONV_01_RUN_DO", "FV_01_POS_AO"} for r in reads)  # one batch
        assert io.get("CONV_01_RUN_DO") is True  # BOOL in, as bool
        plc.calls.clear()
        io.set_many({"PE_01_DI": True, "LT_01_AI": 0.75}, Writer.SIM)
        await until(lambda: io.get("FV_01_POS_AO") == 1.5 and io.get("CONV_01_RUN_DO") is False)
        writes = [names for kind, names in plc.calls if kind == "write"]
        assert writes and set(writes[0]) == {"PE_01_DI", "LT_01_AI"}  # only what changed
        assert len(writes) <= 2  # no rewrite of unchanged values
        cycles, calls = c.state.cycles, len(plc.calls)
        await until(lambda: c.state.cycles >= cycles + 5)
        quiet = plc.calls[calls:]
        assert all(kind == "read" for kind, _ in quiet)  # nothing changed: reads only
        assert isinstance(io.get("FV_01_POS_AO"), float)
        await c.shutdown()
        await task
        assert c.status()["connected"] is False and c.status()["reconnects"] == 0

    run(main())


def test_the_heartbeat_is_written_to_the_controller_when_it_changes():
    async def main():
        io, plc = table(), Controller(dict(TYPES), logic)
        c = client(io, plc)
        task = asyncio.create_task(c.serve_forever())
        await until(lambda: c.state.cycles >= 2)
        for value in (True, False, True):
            io.set("SIM_HEARTBEAT_DI", value, Writer.SIM)
            await until(lambda v=value: plc.values["SIM_HEARTBEAT_DI"] is v)
        await c.shutdown()
        await task

    run(main())


def test_a_missing_and_a_mismatched_tag_are_reported_and_left_out_and_the_rest_runs(caplog):
    caplog.set_level(logging.WARNING)
    types = dict(TYPES)
    del types["FV_01_POS_AO"]  # missing in the controller
    types["LT_01_AI"] = "DINT"  # the wrong type

    async def main():
        io, plc = table(), Controller(types, lambda v: v.update(CONV_01_RUN_DO=not v["PE_01_DI"]))
        c = client(io, plc)
        task = asyncio.create_task(c.serve_forever())
        await until(lambda: c.state.cycles >= 3)
        status = c.status()
        assert status["missing"] == ["FV_01_POS_AO"]
        assert status["mismatched"] == {"LT_01_AI": "DINT, expected REAL"}
        assert all("FV_01_POS_AO" not in n and "LT_01_AI" not in n for _, n in plc.calls)
        io.set("PE_01_DI", True, Writer.SIM)
        await until(lambda: io.get("CONV_01_RUN_DO") is False)  # the rest keeps running
        await c.shutdown()
        await task

    run(main())
    assert "tags missing in the controller: ['FV_01_POS_AO']" in caplog.text
    assert "LT_01_AI is DINT, expected REAL" in caplog.text


def test_a_tag_rejected_in_a_read_is_left_out_without_dropping_the_link():
    async def main():
        io, plc = table(), Controller(dict(TYPES), logic)
        c = client(io, plc)
        task = asyncio.create_task(c.serve_forever())
        await until(lambda: c.state.cycles >= 1)
        del plc.types["CONV_01_RUN_DO"]  # deleted online: the next read reports it
        await until(lambda: "CONV_01_RUN_DO" in c.status()["missing"])
        reads = c.state.reads
        await until(lambda: c.state.reads >= reads + 3)
        assert c.status()["connected"] and c.status()["reconnects"] == 0
        await c.shutdown()
        await task

    run(main())


@pytest.mark.parametrize("mode", ["hold", "off"])
def test_a_10_s_drop_resumes_with_no_restart_and_the_controller_gets_the_new_inputs(mode, caplog):
    """The link drops for 10 s: the client logs it once, backs off, reconnects by itself (the
    same client, the same task), writes every input again, and the controller's logic sees the
    input changed during the outage. With on_link_loss "hold" the PLC's last outputs stay; with
    "off" (the default) they go off after link_loss_timeout_s, in one write, logged once, and
    come back from the controller after the reconnect."""
    caplog.set_level(logging.INFO)

    async def main():
        io, plc = table(), Controller(dict(TYPES), logic)
        c = client(io, plc, backoff_s=(0.1, 0.2, 0.5, 1.0), on_link_loss=mode)
        writes: list[set[str]] = []
        task = asyncio.create_task(c.serve_forever())
        io.set("LT_01_AI", 0.3, Writer.SIM)
        await until(lambda: io.get("CONV_01_RUN_DO") is True and io.get("FV_01_POS_AO") == 0.6)
        batch: set[str] = set()
        io.on_change(lambda name, old, new, writer: batch.add(name))  # noqa: ARG005
        plc.drop()
        t_drop = time.monotonic()
        await until(lambda: not c.status()["connected"])
        io.set_many({"PE_01_DI": True, "LT_01_AI": 0.4}, Writer.SIM)  # during the outage
        batch.clear()
        if mode == "off":
            await until(lambda: c.status()["outputs_off"], timeout=3.0)
            assert time.monotonic() - t_drop >= 1.0  # not before the timeout
            writes.append(set(batch))
        await asyncio.sleep(10.0)
        expected = (True, 0.6) if mode == "hold" else (False, 0.0)
        assert (io.get("CONV_01_RUN_DO"), io.get("FV_01_POS_AO")) == expected
        assert plc.values["PE_01_DI"] is False  # nothing reached the controller
        opens = plc.opens
        plc.restore()
        await until(lambda: c.status()["connected"], timeout=5.0)
        await until(lambda: io.get("CONV_01_RUN_DO") is False and io.get("FV_01_POS_AO") == 0.8)
        assert plc.opens == opens + 1 and not task.done()
        status = c.status()
        assert status["reconnects"] == 1 and status["connects"] == 2
        assert "CommError" in status["last_error"]
        assert status["output_drops"] == (1 if mode == "off" else 0)
        assert status["outputs_off"] is False  # restored by the first read
        if mode == "off":
            assert writes == [{"CONV_01_RUN_DO", "FV_01_POS_AO"}]  # both, in the one write
        await c.shutdown()
        await task

    run(main())
    lost = [r for r in caplog.records if "link lost" in r.getMessage()]
    assert len(lost) == 1  # one line per outage, not per attempt
    assert any("reconnected" in r.getMessage() for r in caplog.records)
    off = [r for r in caplog.records if "every PLC output set off" in r.getMessage()]
    assert len(off) == (1 if mode == "off" else 0)


def test_the_link_loss_timeout_is_measured_from_the_last_good_exchange():
    """A stalled controller (calls hang, no error yet) trips "off" on the timeout, measured on an
    injected clock: not at 0.9 s of silence with a 1.0 s timeout, at 1.1 s yes, and only once."""
    now = [100.0]

    async def main():
        io, plc = table(), Controller(dict(TYPES), logic)
        c = client(io, plc, on_link_loss="off", link_loss_timeout_s=1.0, clock=lambda: now[0])
        task = asyncio.create_task(c.serve_forever())
        await until(lambda: io.get("CONV_01_RUN_DO") is True)
        plc.drop()  # the cycle now fails; the fake clock decides "how long"
        await until(lambda: not c.status()["connected"])
        last_ok = c._last_ok
        now[0] = last_ok + 0.9
        await asyncio.sleep(0.2)
        assert not c.status()["outputs_off"] and io.get("CONV_01_RUN_DO") is True
        now[0] = last_ok + 1.1
        await until(lambda: c.status()["outputs_off"])
        assert io.get("CONV_01_RUN_DO") is False
        now[0] = last_ok + 30.0
        await asyncio.sleep(0.2)
        assert c.status()["output_drops"] == 1  # once per outage
        await c.shutdown()
        await task

    run(main())


def test_the_connection_entry_takes_on_link_loss_and_its_timeout(tmp_path):
    from openfactory.core.config import load_settings

    (tmp_path / "openfactory.local.toml").write_text(
        '[connections.a]\nprotocol = "logix"\npath = "192.0.2.10"\n'
        '[connections.b]\nprotocol = "logix"\npath = "192.0.2.11"\non_link_loss = "hold"\n'
        "link_loss_timeout_s = 2.5\n",
        encoding="utf-8",
    )
    conns = load_settings(tmp_path).connections
    assert (conns["a"].on_link_loss, conns["a"].link_loss_timeout_s) == ("off", 1.0)
    assert (conns["b"].on_link_loss, conns["b"].link_loss_timeout_s) == ("hold", 2.5)
    (tmp_path / "openfactory.local.toml").write_text(
        '[connections.a]\nprotocol = "logix"\npath = "x"\non_link_loss = "maybe"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="on_link_loss"):
        load_settings(tmp_path)


def test_an_unreachable_controller_does_not_hold_up_the_run():
    async def main():
        io, plc = table(), Controller(dict(TYPES), logic)
        plc.up = False
        c = client(io, plc)
        task = asyncio.create_task(c.serve_forever())
        t0 = time.monotonic()
        await c.wait_ready(timeout=5)
        assert time.monotonic() - t0 < 1.0 and not c.status()["connected"]
        plc.up = True
        await until(lambda: c.status()["connected"])
        await c.shutdown()
        await task

    run(main())


@pytest.mark.parametrize("dtype,logix", [("bool", "BOOL"), ("float32", "REAL"), ("int16", "INT")])
def test_the_type_map(dtype, logix):
    from openfactory.protocols.logix import LOGIX_TYPE

    assert LOGIX_TYPE[dtype] == logix


# --- the CLI: `openfactory run SCENE --logix NAME` resolves the named connection (ADR-0002) ----

from pathlib import Path  # noqa: E402

from typer.testing import CliRunner  # noqa: E402

from openfactory.cli import app  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _cli(tmp_path: Path, monkeypatch, toml: str, *extra: str):  # noqa: ANN001, ANN202
    scene = tmp_path / "sorting-by-height.yaml"  # `run` writes lockfiles next to the scene
    scene.write_text((REPO / "scenes" / scene.name).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "openfactory.local.toml").write_text(toml, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = ["run", str(scene), "--as-fast-as-possible", "--duration", "0.5", "-q", *extra]
    return CliRunner().invoke(app, args)


def test_run_names_what_is_wrong_with_the_connection(tmp_path, monkeypatch):
    bench = '[connections.bench]\nprotocol = "logix"\npath = "127.0.0.1"\n'
    out = _cli(tmp_path, monkeypatch, bench, "--logix", "nope")
    assert out.exit_code == 1 and "no [connections.nope]" in out.output and "bench" in out.output
    modbus = '[connections.bench]\nprotocol = "modbus"\npath = "127.0.0.1"\n'
    out = _cli(tmp_path, monkeypatch, modbus, "--logix", "bench")
    assert out.exit_code == 1 and "protocol 'modbus', not 'logix'" in out.output
    script = str(REPO / "examples" / "pyplc" / "sorting-by-height.py")
    out = _cli(tmp_path, monkeypatch, bench, "--logix", "bench", "--script", script)
    assert out.exit_code == 1 and "--script and a Logix controller" in out.output


def test_run_with_an_unreachable_controller_still_runs_and_keeps_trying(tmp_path, monkeypatch):
    """pycomm3's real LogixDriver, its socket open made to refuse as if nothing listened on
    44818: the run goes ahead and finishes; the controller is simply not there."""
    unreachable = '[connections.bench]\nprotocol = "logix"\npath = "127.0.0.1"\n'
    monkeypatch.setattr("pycomm3.cip_driver.CIPDriver.open", _refuse)
    out = _cli(tmp_path, monkeypatch, unreachable, "--logix", "bench")
    assert out.exit_code == 0, out.output


def _refuse(self):  # noqa: ANN001, ANN202 - stands in for nothing listening on 44818
    raise CommError("Failed to open socket to 127.0.0.1:44818")


# --- PLAN 5.3b: the Logix path by plant names --------------------------------------------------
#
# A second fake controller whose definitions are in pycomm3's own shape (``tags``: a dict of
# ``{tag_type, data_type, data_type_name}``, a structure's members under
# ``data_type["internal_tags"]``, a BOOL member with ``bit``), and which looks names up and parses
# requests with pycomm3 1.2.16's own ``LogixDriver.get_tag_info``, ``_get_tag_info`` and
# ``_parse_tag_request``, so the client is checked against pycomm3's lookup, not a copy of it.

import copy  # noqa: E402
import re  # noqa: E402

import yaml  # noqa: E402
from pycomm3 import LogixDriver  # noqa: E402
from pycomm3.exceptions import RequestError  # noqa: E402

from openfactory.core.loader import build  # noqa: E402
from openfactory.core.schema import parse_scene  # noqa: E402
from openfactory.protocols.logix import plant_names  # noqa: E402


def atomic(t: str, dim: int = 0) -> dict:
    return {"tag_type": "atomic", "data_type": t, "data_type_name": t, "dim": dim}


def member(t: str, bit: int | None = None) -> dict:
    info = {"tag_type": "atomic", "data_type": t, "data_type_name": t, "offset": 0}
    return {**info, "bit": bit} if bit is not None else {**info, "array": 0}


def struct(name: str, members: dict) -> dict:
    return {"tag_type": "struct", "data_type": {"name": name, "internal_tags": members},
            "data_type_name": name, "dim": 0}  # fmt: skip


#: The plant's controller (the Done-when): a program-scoped tag, a structure with a BOOL member
#: and a REAL member, and a DINT whose bit 4 is a DI; plus what the rest of the sorter names.
PLANT_DEFS = {
    "Program:Sorter.Conv1_Run": atomic("BOOL"),
    "Conv1": struct("CONV", {"Running": member("BOOL", 0), "Speed": member("REAL")}),
    "Conv2": struct(
        "CONV", {"Running": member("BOOL", 0), "Run": member("BOOL", 1), "Speed": member("REAL")}
    ),  # fmt: skip
    "Pusher": struct(
        "PUSH",
        {"Extend": member("BOOL", 0), "Extended": member("BOOL", 1), "Count": member("DINT")},
    ),  # fmt: skip
    "Sorter_DI_Word": atomic("DINT"),
    "Sorter_DO_Word": atomic("DINT"),
    "Speeds": atomic("REAL", dim=1),
    "Conv1_Speed": atomic("REAL"),
    "Small": atomic("SINT"),
    "Mid": atomic("INT"),
}
#: The sorter's plant names (test_plc_tag.py's PLANT), its first belt's speed on the structure's
#: REAL member so that both member types are exchanged.
SORTER_NAMES = {
    "CONV_01_RUN_DO": "Program:Sorter.Conv1_Run",  # program-scoped
    "CONV_01_SPD_AO": "Conv1.Speed",  # a structure's REAL member
    "CONV_01_RUN_DI": "Conv1.Running",  # a structure's BOOL member
    "PE_01_DI": "Sorter_DI_Word.4",  # bit 4 of a DINT
    "CYL_01_EXT_DO": "Pusher.Extend",
    "CYL_01_EXT_DI": "Pusher.Extended",
    "CYL_01_RET_DI": "Sorter_DI_Word.5",
    "EMT_01_EN_DO": "Sorter_DO_Word.0",
    "CONV_02_RUN_DO": "Conv2.Run",
    "CONV_02_SPD_AO": "Speeds[2]",  # an element
    "CONV_02_RUN_DI": "Conv2.Running",
}
SORTER_HEARTBEAT = "Sorter_DI_Word.0"


def zero(info: dict):  # noqa: ANN201
    if info["tag_type"] == "struct":
        return {k: zero(m) for k, m in info["data_type"]["internal_tags"].items()}
    value = {"BOOL": False, "REAL": 0.0}.get(info["data_type_name"], 0)
    return [value] * 4 if info.get("dim") else value


class PlantController(Controller):
    """Memory by base tag: a structure a dict of members, an array a list, a DINT an int."""

    def __init__(self, defs: dict, logic: Callable[[dict], None] | None = None) -> None:
        super().__init__({}, logic)
        self.defs = copy.deepcopy(defs)
        self.memory = {name: zero(info) for name, info in self.defs.items()}


class PlantDriver:
    """pycomm3's lookups on PlantController's memory; reads and writes resolve a name as
    ``_parse_tag_request`` parses it (base, members, element, bit)."""

    get_tag_info = LogixDriver.get_tag_info
    _get_tag_info = LogixDriver._get_tag_info
    _parse_tag_request = LogixDriver._parse_tag_request
    _LogixDriver__log = logging.getLogger("fake-logix")

    def __init__(self, plc: PlantController) -> None:
        self.plc = plc
        self.epoch: int | None = None

    @property
    def _tags(self) -> dict:
        return self.plc.defs

    tags = _tags

    open, close, _alive = FakeDriver.open, FakeDriver.close, FakeDriver._alive
    _result = FakeDriver._result

    def _slot(self, plc_tag: str):  # noqa: ANN202 - (container, key) holding the value
        base, *attrs = plc_tag.split(".")
        if base.startswith("Program:"):
            base = f"{base}.{attrs.pop(0)}"
        holder, key = self.plc.memory, base
        for part in attrs:
            holder, key = self._index(holder, key), part
        return self._final(holder, key)

    @staticmethod
    def _index(holder, key):  # noqa: ANN001, ANN205
        name, _, idx = key.partition("[")
        value = holder[name]
        return value[int(idx[:-1])] if idx else value

    @staticmethod
    def _final(holder, key):  # noqa: ANN001, ANN205
        name, _, idx = key.partition("[")
        return (holder[name], int(idx[:-1])) if idx else (holder, name)

    def read(self, *names: str):
        self._alive()
        self.plc.calls.append(("read", names))
        if self.plc.logic:
            self.plc.logic(self)
        out = []
        for n in names:
            try:
                req = self._parse_tag_request(n)
            except RequestError as exc:
                out.append(PyTag(n, None, None, str(exc)))
                continue
            holder, key = self._slot(req["plc_tag"])
            value = holder[key]
            if req["bit"] is not None:
                value = bool(value >> req["bit"] & 1)
            out.append(PyTag(n, value, req["tag_info"]["data_type_name"], None))
        return self._result(out)

    def write(self, *pairs: tuple[str, object]):
        self._alive()
        self.plc.calls.append(("write", tuple(n for n, _ in pairs)))
        out = []
        for n, v in pairs:
            try:
                req = self._parse_tag_request(n, "w")
            except RequestError as exc:
                out.append(PyTag(n, None, None, str(exc)))
                continue
            self.set(req, v)
            out.append(PyTag(n, v, req["tag_info"]["data_type_name"], None))
        return self._result(out)

    def set(self, req: dict, value: object) -> None:
        holder, key = self._slot(req["plc_tag"])
        if req["bit"] is not None:  # pycomm3 sends a read-modify-write of the bit
            assert type(value) is bool, (req, value)
            mask = 1 << req["bit"]
            holder[key] = holder[key] | mask if value else holder[key] & ~mask
        else:
            want = {"BOOL": bool, "REAL": float, "DINT": int}[req["tag_info"]["data_type_name"]]
            assert type(value) is want, (req, value)
            holder[key] = value

    def get(self, name: str):  # noqa: ANN201 - for the controller's logic and the asserts
        return self.read(name).value


def sorter_scene(names: dict[str, str], heartbeat: str | None = SORTER_HEARTBEAT):  # noqa: ANN201
    doc = yaml.safe_load((REPO / "scenes" / "sorting-by-height.yaml").read_text(encoding="utf-8"))
    for comp in doc["components"]:
        for port, binding in (comp.get("io") or {}).items():
            spec = binding if isinstance(binding, dict) else {"tag": binding}
            if spec["tag"] in names:
                comp["io"][port] = {**spec, "plc_tag": names[spec["tag"]]}
    if heartbeat:
        doc["plc"]["heartbeat_plc_tag"] = heartbeat
    scene = parse_scene(yaml.safe_dump(doc, sort_keys=False), REPO / "scenes")
    return scene, build(scene, base_dir=REPO / "scenes")


def sorter_logic(d: PlantDriver) -> None:
    """The plant's program: belt 1 runs while the eye (Sorter_DI_Word.4) is clear, at a fixed
    speed; the pusher extends while the eye sees a box; the emitter's enable bit is on."""
    mem = d.plc.memory
    eye = bool(mem["Sorter_DI_Word"] >> 4 & 1)
    mem["Program:Sorter.Conv1_Run"] = not eye
    mem["Conv1"]["Speed"] = 0.5
    mem["Pusher"]["Extend"] = eye
    mem["Sorter_DO_Word"] |= 1
    mem["Conv2"]["Run"] = True
    mem["Speeds"][2] = 0.25


def plant_client(io: IOTable, plc: PlantController, plant: dict, **kw) -> LogixClient:
    kw.setdefault("period_s", 0.01)
    kw.setdefault("backoff_s", (0.05, 0.1, 0.2))
    return LogixClient(io, "192.0.2.10", driver_factory=lambda path: PlantDriver(plc),
                       plant=plant, **kw)  # fmt: skip


def test_the_sorter_by_plant_names_one_write_and_one_read_per_cycle():
    """PLAN 5.3b's Done-when: a program-scoped tag, a structure's BOOL and REAL members, a DINT
    whose bit 4 is a DI, an element and the heartbeat as a bit, all in one write and one read a
    cycle, and every value put back under its scene tag."""
    scene, loaded = sorter_scene(SORTER_NAMES)
    io, plant = loaded.io, plant_names(scene)
    assert plant["SIM_HEARTBEAT_DI"] == SORTER_HEARTBEAT and len(plant) == 12

    async def main():
        plc = PlantController(PLANT_DEFS, sorter_logic)
        c = plant_client(io, plc, plant)
        assert c.init_program_tags is True  # a Program: name needs the program definitions
        task = asyncio.create_task(c.serve_forever())
        await until(lambda: c.state.cycles >= 3)
        assert c.status()["missing"] == [] and c.status()["mismatched"] == {}
        sim = {plant[t.name] for t in io.tags() if t.kind in ("DI", "AI")}
        out = {plant[t.name] for t in io.tags() if t.kind in ("DO", "AO")}
        first_write = next(names for kind, names in plc.calls if kind == "write")
        assert set(first_write) == sim  # every input, by its plant name, in one write
        reads = [names for kind, names in plc.calls if kind == "read"]
        assert reads and all(set(r) == out for r in reads)  # every output in one read
        # a cycle is at most one write, then one read
        kinds = "".join(kind[0] for kind, _ in plc.calls)
        assert re.fullmatch("(w?r)+", kinds), kinds
        # the values, under their scene tags
        assert io.get("CONV_01_RUN_DO") is True  # Program:Sorter.Conv1_Run
        assert io.get("CONV_01_SPD_AO") == pytest.approx(0.5)  # Conv1.Speed, a REAL member
        assert io.get("EMT_01_EN_DO") is True  # Sorter_DO_Word.0, a bit read
        assert io.get("CONV_02_SPD_AO") == pytest.approx(0.25)  # Speeds[2], an element
        assert io.get("CONV_02_RUN_DO") is True  # Conv2.Run, a BOOL member
        io.set("PE_01_DI", True, Writer.SIM)  # bit 4 of the DINT
        io.set("CONV_01_RUN_DI", True, Writer.SIM)  # a structure's BOOL member, written
        await until(lambda: io.get("CYL_01_EXT_DO") is True and io.get("CONV_01_RUN_DO") is False)
        assert plc.memory["Sorter_DI_Word"] >> 4 & 1 == 1 and plc.memory["Conv1"]["Running"]
        io.set("SIM_HEARTBEAT_DI", True, Writer.SIM)
        await until(lambda: plc.memory["Sorter_DI_Word"] & 1 == 1)  # bit 0: the heartbeat
        assert plc.memory["Sorter_DI_Word"] >> 4 & 1 == 1  # bit 4 kept
        await c.shutdown()
        await task

    run(main())


@pytest.mark.parametrize(
    ("scene_tag", "name", "why"),
    [
        # a structure member of the wrong type: mismatched and left out
        ("CYL_01_EXT_DO", "Pusher.Count", "Pusher.Count is DINT, expected BOOL"),
        # a bit on a REAL: refused at connect
        ("PE_01_DI", "Conv1_Speed.3", "Conv1_Speed.3 is a bit of a REAL; a bit is a bit of a "
         "SINT, INT or DINT (1756-PM004L-EN-P, ch. 2, Address tag data)"),
        # bits past the type's width: 0 to 7 for a SINT, 0 to 15 for an INT
        ("PE_01_DI", "Small.8", "Small.8 is bit 8 of a SINT, whose bits are 0 to 7"),
        ("PE_01_DI", "Mid.16", "Mid.16 is bit 16 of an INT, whose bits are 0 to 15"),
        # a bit names a BOOL; a REAL point cannot be one
        ("CONV_01_SPD_AO", "Sorter_DO_Word.9", "Sorter_DO_Word.9 is a bit, expected REAL"),
    ],
)  # fmt: skip
def test_a_wrong_type_or_a_bit_out_of_range_is_mismatched_and_left_out(scene_tag, name, why):
    scene, loaded = sorter_scene({**SORTER_NAMES, scene_tag: name})
    io, plant = loaded.io, plant_names(scene)

    async def main():
        plc = PlantController(PLANT_DEFS, sorter_logic)
        c = plant_client(io, plc, plant)
        task = asyncio.create_task(c.serve_forever())
        await until(lambda: c.state.cycles >= 3)
        mismatched = c.status()["mismatched"]
        assert list(mismatched) == [scene_tag] and why in mismatched[scene_tag]
        assert all(name not in names for _, names in plc.calls)  # left out of every batch
        assert io.get("EMT_01_EN_DO") is True  # the rest keeps running
        await c.shutdown()
        await task

    run(main())


@pytest.mark.parametrize("name", ["Small.7", "Mid.15", "Sorter_DI_Word.31"])
def test_the_last_bit_of_each_integer_type_is_accepted(name):
    scene, loaded = sorter_scene({**SORTER_NAMES, "PE_01_DI": name})

    async def main():
        plc = PlantController(PLANT_DEFS, sorter_logic)
        c = plant_client(loaded.io, plc, plant_names(scene))
        task = asyncio.create_task(c.serve_forever())
        await until(lambda: c.state.cycles >= 2)
        assert c.status()["mismatched"] == {} and c.status()["missing"] == []
        await c.shutdown()
        await task

    run(main())


def test_a_name_the_controller_lacks_is_missing_naming_the_plant_name(caplog):
    caplog.set_level(logging.WARNING)
    scene, loaded = sorter_scene({**SORTER_NAMES, "CONV_02_RUN_DI": "Conv9.Running"})

    async def main():
        plc = PlantController(PLANT_DEFS, sorter_logic)
        c = plant_client(loaded.io, plc, plant_names(scene))
        task = asyncio.create_task(c.serve_forever())
        await until(lambda: c.state.cycles >= 2)
        assert c.status()["missing"] == ["CONV_02_RUN_DI"]
        await c.shutdown()
        await task

    run(main())
    assert "CONV_02_RUN_DI (Conv9.Running)" in caplog.text


def test_program_tags_are_uploaded_only_when_a_plant_name_needs_them(monkeypatch):
    """pycomm3's LogixDriver is opened with init_program_tags=True only when a plant name starts
    with Program: (its __init__ docstring: initialization of tags is required for read and
    write); a scene with no plc_tag keeps init_program_tags=False, as before 5.3b."""
    made: list[dict] = []
    monkeypatch.setattr("pycomm3.LogixDriver", lambda path, **kw: made.append(kw) or object())
    scene, loaded = sorter_scene(SORTER_NAMES)
    LogixClient(loaded.io, "192.0.2.10", plant=plant_names(scene))._factory("192.0.2.10")
    no_program = {k: v for k, v in SORTER_NAMES.items() if not v.startswith("Program:")}
    scene, loaded = sorter_scene(no_program)
    LogixClient(loaded.io, "192.0.2.10", plant=plant_names(scene))._factory("192.0.2.10")
    plain = build(parse_scene((REPO / "scenes" / "sorting-by-height.yaml").read_text(
        encoding="utf-8"), REPO / "scenes"), base_dir=REPO / "scenes")  # fmt: skip
    LogixClient(plain.io, "192.0.2.10")._factory("192.0.2.10")
    assert made == [{"init_program_tags": True}, {"init_program_tags": False},
                    {"init_program_tags": False}]  # fmt: skip
