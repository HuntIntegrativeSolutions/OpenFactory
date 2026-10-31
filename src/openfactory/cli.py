"""``openfactory`` command line (PLAN task 1.10): ``run``, ``test``, ``validate``, ``lint``,
``export``.

Behavior spec
-------------
- ``openfactory run SCENE`` loads the scene, writes or updates its Modbus address lockfile
  (ADR-0004, ``<scene>.<encoding>.modbus.yaml``), starts the Modbus TCP server when
  ``plc.protocol`` is ``modbus_tcp``, starts the viewer and API server (PLAN 1.9) unless
  ``--headless``, and steps the simulation at 50 Hz in real time until Ctrl+C or ``--duration``
  simulated seconds. Logging is at INFO by default, so the register map and listen addresses print
  at startup; ``--quiet`` shows warnings only.
  ``--as-fast-as-possible`` steps with no sleeping (for CI and PLC-logic tests), needs
  ``--duration``, and implies ``--headless``. ``--force TAG=VALUE`` sets a PLC-written tag (DO or
  AO) at startup, as if the PLC had written it, so a scene can be demonstrated without a PLC; a
  real PLC overwrites it on its next write. ``--script FILE.py`` runs a stand-in PLC written in
  Python (``step(io, t)`` every tick, :mod:`openfactory.pyplc`); a script exception stops the run
  with exit code 1. ``--analog-encoding float32|int16_scaled`` overrides
  the scene's ``plc.analog_encoding`` for this run and selects that encoding's lockfile; the other
  encoding's lockfile is left alone. At the end it prints the statistics block (PLAN 2.11:
  throughput, utilization, actuator cycles, dwell, losses; also ``GET /api/stats``) and then a
  one-line run summary, always the last line: ticks, parts spawned, removed and lost by part name,
  and removed per part remover.
- Listen addresses follow ``openfactory.toml`` precedence (:mod:`openfactory.core.config`).
  ``--bind`` applies to every server and is the only way to leave loopback from the command line;
  ``--port`` is the Modbus port and ``--web-port`` the viewer port. With the viewer bound beyond
  loopback, ``/api/fault`` needs ``--fault-token`` or ``[server] fault_token``; with neither, the
  fault API is off and ``run`` logs a one-line notice (PLAN §6.4).
- ``run --exam`` (PLAN 2.11): every DI bound with ``exam: switch`` follows an operator switch
  in the viewer (``/api/exam``) instead of the simulation, starting off, and state frames carry
  no fault markers, so trainees find faults from the PLC's side.
- ``run`` and ``test`` take ``--record FILE.ofrec`` (PLAN 2.12, :mod:`openfactory.record`): the
  run's I/O changes, state frames and events, with the scene itself, so ``openfactory replay
  FILE.ofrec [--speed X] [--loop] [--exit-at-end]`` can show it in the viewer with no scene file and
  no simulation, and ``openfactory replay FILE.ofrec --io`` prints the I/O change log.
- ``run`` and ``test`` take ``--fault ID=KIND[:key=value,...]`` (repeatable) and ``--fault-at T``
  (simulated seconds, default 0): each fault is checked against its component before the run and
  injected between ticks once the simulated time reaches T, logged like an ``/api/fault`` request,
  so a headless run or a graded exercise can include a jammed pusher or a stuck eye (PLAN 2.11).
  ``--fault comms=drop`` or ``--fault comms=latency:ms=N`` stalls or delays the Modbus server
  instead (PLAN 2.13); the simulation keeps running.
- ``openfactory test SCENE --script FILE.py | --plc`` (PLAN 2.11) runs the scene headless for its
  ``checks.duration_s`` (or ``--duration``) and grades the run against its ``checks:`` block
  (:mod:`openfactory.checks`): as fast as possible under a stand-in script, or in real time with
  the Modbus server up while a real PLC controls the plant. It prints one PASS/FAIL line per check
  and a verdict, then the run summary line, and exits 1 if any check fails.
- ``openfactory validate SCENE...`` checks each scene: schema, component build, loader warnings,
  and the Modbus register map against each of its lockfiles that exists (read only). A directory
  is expanded to its ``*.yaml`` scenes, skipping ``*.modbus.yaml`` lockfiles. Exit code 1 if any
  scene fails.
- ``openfactory lint SCENE... [--script FILE.py]`` reports likely scene mistakes
  (:mod:`openfactory.lint`, PLAN 2.20) and exits 1 only on errors.
- ``openfactory export SCENE [-o OUT] [--analog-encoding ENC]`` writes the I/O list CSV (PLAN 1.8)
  to OUT or stdout, and creates or updates that encoding's lockfile like ``run``.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time
from pathlib import Path
from typing import Annotated, Any, get_args

import typer
import uvicorn
from pydantic import ValidationError

from openfactory import __version__
from openfactory.core.config import Endpoint, load_settings, resolve_endpoint
from openfactory.core.iotable import Writer
from openfactory.core.loader import LoadedScene, build
from openfactory.core.schema import PLC_WRITES, Scene, load_scene
from openfactory.protocols.modbus import (
    LOCKFILE_SUFFIX,
    AddressError,
    Encoding,
    ModbusServer,
    lockfile_path,
    scene_register_map,
)

log = logging.getLogger("openfactory")

app = typer.Typer(
    name="openfactory",
    help="OpenFactory: open-source 3D factory simulator for PLC training and demos. "
    "Not a safety-rated simulation.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

ENCODINGS: tuple[str, ...] = get_args(Encoding)
AnalogEncoding = Annotated[
    str | None,
    typer.Option(
        "--analog-encoding",
        help=f"Modbus analog encoding ({' | '.join(ENCODINGS)}); overrides the scene's "
        "plc.analog_encoding and selects <scene>.<encoding>.modbus.yaml.",
    ),
]

EXPORT_FORMATS = ("csv", "l5x", "ignition")
ExportFormat = str

_TRUE = {"1", "true", "on", "yes"}
_FALSE = {"0", "false", "off", "no"}


def _setup_logging(quiet: bool) -> None:
    logging.basicConfig(
        level=logging.WARNING if quiet else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def _load(path: Path) -> tuple[Scene, LoadedScene]:
    if path.name.endswith(LOCKFILE_SUFFIX):
        raise ValueError(f"{path} is a Modbus address lockfile, not a scene")
    scene = load_scene(path)
    return scene, build(scene)


def _encoding(value: str | None) -> Encoding | None:
    if value is not None and value not in ENCODINGS:
        raise typer.BadParameter(
            f"{value!r}: expected one of {', '.join(ENCODINGS)}", param_hint="--analog-encoding"
        )
    return value  # type: ignore[return-value]


def _apply_forces(loaded: LoadedScene, forces: list[str]) -> None:
    for item in forces:
        name, sep, raw = item.partition("=")
        if not sep:
            raise typer.BadParameter(f"{item!r}: expected TAG=VALUE", param_hint="--force")
        if name not in loaded.io:
            raise typer.BadParameter(f"unknown tag {name!r}", param_hint="--force")
        tag = loaded.io.tag(name)
        if tag.kind not in PLC_WRITES:
            raise typer.BadParameter(
                f"{name} is a {tag.kind}; only PLC-written DO/AO tags can be forced",
                param_hint="--force",
            )
        if tag.kind == "DO":
            if raw.lower() not in _TRUE | _FALSE:
                raise typer.BadParameter(f"{item!r}: DO value must be 1/0", param_hint="--force")
            value: bool | float = raw.lower() in _TRUE
        else:
            try:
                value = float(raw)
            except ValueError:
                raise typer.BadParameter(f"{item!r}: AO value must be a number") from None
        loaded.io.set(name, value, Writer.PLC)
        log.info("forced %s = %s", name, value)


async def _serve(
    runtime,  # openfactory.server.runtime.Runtime (imported lazily with the web stack)
    servers: list,  # protocol servers: ModbusServer, OpcUaServer (serve_forever/wait_ready)
    web: uvicorn.Server | None,
    duration: float | None,
) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):  # Windows
            loop.add_signal_handler(sig, stop.set)
    tasks = []
    for server in servers:
        tasks.append(asyncio.create_task(server.serve_forever()))
        await server.wait_ready()
    # The simulation starts before the viewer server, so the API never shows a sim not yet running.
    runtime.start(duration)
    try:
        if web is not None:
            tasks.append(asyncio.create_task(web.serve()))
            while not web.started:
                if tasks[-1].done():
                    await tasks[-1]  # surfaces a bind error
                await asyncio.sleep(0.02)
        while runtime.running and not stop.is_set() and not (web and web.should_exit):
            await asyncio.sleep(0.05)
    finally:
        runtime.stop()
        for server in servers:
            await server.shutdown()
        if web is not None:
            web.should_exit = True
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, 5)


@app.command()
def run(
    scene: Annotated[
        Path, typer.Argument(help="Scene file (scenes/<name>.yaml).", metavar="SCENE")
    ],
    headless: Annotated[bool, typer.Option(help="No viewer or API server.")] = False,
    as_fast_as_possible: Annotated[
        bool,
        typer.Option(
            "--as-fast-as-possible",
            help="Step with no sleeping (CI, PLC logic tests). Needs --duration; implies "
            "--headless.",
        ),
    ] = False,
    duration: Annotated[
        float | None, typer.Option(help="Stop after this many simulated seconds.", min=0)
    ] = None,
    bind: Annotated[
        str | None,
        typer.Option(help="Listen address for every server (e.g. 0.0.0.0). Default: loopback."),
    ] = None,
    port: Annotated[int | None, typer.Option(help="Modbus TCP port (default 5020).")] = None,
    web_port: Annotated[int | None, typer.Option(help="Viewer/API port (default 8000).")] = None,
    opcua: Annotated[
        bool,
        typer.Option(
            "--opcua",
            help="Also serve the I/O table over OPC UA (PLAN 2.4; for Ignition as the HMI). "
            "Needs the opcua extra.",
        ),
    ] = False,
    opcua_port: Annotated[
        int | None, typer.Option(help="OPC UA port with --opcua (default 4840).")
    ] = None,
    force: Annotated[
        list[str] | None,
        typer.Option(help="TAG=VALUE: set a DO/AO tag at startup as if the PLC wrote it."),
    ] = None,
    analog_encoding: AnalogEncoding = None,
    script: Annotated[
        Path | None,
        typer.Option(
            help="Python file defining step(io, t): a stand-in PLC scanned every tick (PLAN 2.10)."
        ),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Warnings only.")] = False,
    record: Annotated[
        Path | None,
        typer.Option(
            help="Record the run to FILE.ofrec (PLAN 2.12): replay it with `openfactory replay`.",
        ),
    ] = None,
    fault: Annotated[
        list[str] | None,
        typer.Option(
            help="ID=KIND[:key=value,...]: inject a fault (PLAN 2.6) at --fault-at; repeatable.",
        ),
    ] = None,
    fault_at: Annotated[
        float, typer.Option(help="Simulated time, seconds, to inject the --fault faults.", min=0)
    ] = 0.0,
    exam: Annotated[
        bool,
        typer.Option(
            "--exam",
            help="Exam mode (PLAN 2.11): DIs bound with `exam: switch` follow operator switches "
            "in the viewer instead of the simulation, and fault markers are hidden.",
        ),
    ] = False,
    fault_token: Annotated[
        str | None,
        typer.Option(
            help="Token /api/fault requires when the viewer is bound beyond loopback (PLAN 6.4); "
            "prefer [server] fault_token in openfactory.local.toml, which ps cannot show.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Run a scene: Modbus server, viewer, and the 50 Hz simulation."""
    _setup_logging(quiet)
    encoding = _encoding(analog_encoding)
    if as_fast_as_possible and duration is None:
        raise typer.BadParameter("--as-fast-as-possible needs --duration", param_hint="--duration")
    headless = headless or as_fast_as_possible
    try:
        parsed, loaded = _load(scene)
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"error: {scene}: {exc}", err=True)
        raise typer.Exit(1) from None
    log.info("OpenFactory %s: %s (training and demo tool, not safety-rated)", __version__, scene)

    _apply_forces(loaded, force or [])
    faults = _faults(loaded, fault or [], fault_at)
    if exam:
        switches = loaded.enable_exam()
        if switches:
            log.info("exam mode: operator switches for %s (all off)", ", ".join(switches))
        else:
            log.warning("exam mode: the scene binds no DI with `exam: switch`; nothing to drive")
    runtime, wall = _simulate(
        scene,
        parsed,
        loaded,
        encoding=encoding,
        headless=headless,
        fast=as_fast_as_possible,
        duration=duration,
        bind=bind,
        port=port,
        web_port=web_port,
        script=script,
        fault_token=fault_token,
        serve_opcua=opcua,
        opcua_port=opcua_port,
        faults=faults,
        record=record,
    )
    _statistics(runtime)
    _summary(loaded, wall)


@app.command()
def test(
    scene: Annotated[
        Path, typer.Argument(help="Scene file with a checks: block.", metavar="SCENE")
    ],
    script: Annotated[
        Path | None,
        typer.Option(help="Grade a stand-in PLC script (step(io, t)); runs as fast as possible."),
    ] = None,
    plc: Annotated[
        bool,
        typer.Option(
            "--plc",
            help="Grade a real PLC: serve Modbus and run in real time while the PLC controls it.",
        ),
    ] = False,
    duration: Annotated[
        float | None,
        typer.Option(help="Simulated seconds to run (default: checks.duration_s).", min=0),
    ] = None,
    bind: Annotated[
        str | None,
        typer.Option(help="Listen address for the Modbus server with --plc. Default: loopback."),
    ] = None,
    port: Annotated[int | None, typer.Option(help="Modbus TCP port (default 5020).")] = None,
    analog_encoding: AnalogEncoding = None,
    record: Annotated[
        Path | None,
        typer.Option(
            help="Record the run to FILE.ofrec (PLAN 2.12): replay it with `openfactory replay`.",
        ),
    ] = None,
    fault: Annotated[
        list[str] | None,
        typer.Option(
            help="ID=KIND[:key=value,...]: inject a fault (PLAN 2.6) at --fault-at; repeatable.",
        ),
    ] = None,
    fault_at: Annotated[
        float, typer.Option(help="Simulated time, seconds, to inject the --fault faults.", min=0)
    ] = 0.0,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Warnings only.")] = False,
) -> None:
    """Grade a PLC program against the scene's checks (PLAN 2.11); exit 1 if any check fails."""
    from openfactory.checks import evaluate, format_results

    _setup_logging(quiet)
    if (script is None) == (not plc):
        raise typer.BadParameter("give exactly one of --script FILE.py or --plc")
    encoding = _encoding(analog_encoding)
    try:
        parsed, loaded = _load(scene)
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"error: {scene}: {exc}", err=True)
        raise typer.Exit(1) from None
    if parsed.checks is None:
        typer.echo(f"error: {scene}: the scene has no checks: block to grade against", err=True)
        raise typer.Exit(1)
    faults = _faults(loaded, fault or [], fault_at)
    seconds = duration if duration is not None else parsed.checks.duration_s
    log.info(
        "OpenFactory %s: testing %s for %g s with %s", __version__, scene, seconds,
        f"--script {script}" if script else "a PLC over Modbus",
    )  # fmt: skip
    runtime, wall = _simulate(
        scene,
        parsed,
        loaded,
        encoding=encoding,
        headless=True,
        fast=script is not None,
        duration=seconds,
        bind=bind,
        port=port,
        web_port=None,
        script=script,
        fault_token=None,
        serve_modbus=plc,  # a script needs no Modbus server; the lockfile is still checked
        faults=faults,
        record=record,
    )
    results = evaluate(parsed.checks, loaded)
    typer.echo(format_results(results))
    _statistics(runtime)
    _summary(loaded, wall)
    if not all(r.passed for r in results):
        raise typer.Exit(1)


def _simulate(
    scene: Path,
    parsed: Scene,
    loaded: LoadedScene,
    *,
    encoding: Encoding | None,
    headless: bool,
    fast: bool,
    duration: float | None,
    bind: str | None,
    port: int | None,
    web_port: int | None,
    script: Path | None,
    fault_token: str | None,
    serve_modbus: bool = True,
    serve_opcua: bool = False,
    opcua_port: int | None = None,
    faults: list[tuple[float, str, str, dict[str, Any]]] | None = None,
    record: Path | None = None,
):  # -> tuple[Runtime, float]; Runtime is imported lazily with the web stack
    """Serve and step a loaded scene until ``duration`` (or Ctrl+C); returns the runtime and
    the wall time it took. Exits 1 on a lockfile conflict, a script load error, or a script
    exception. Shared by ``run`` and ``test``."""
    from openfactory.server.app import (  # web stack only when running
        create_app,
        fault_api_notice,
        server_config,
    )
    from openfactory.server.runtime import Runtime

    settings = load_settings()
    modbus = None
    if parsed.plc.protocol == "modbus_tcp":
        try:
            register_map = scene_register_map(parsed, encoding, scene_path=scene)
        except AddressError as exc:
            typer.echo(f"error: {scene}: {exc}", err=True)
            raise typer.Exit(1) from None
        log.info("address lockfile: %s", lockfile_path(scene, register_map.encoding))
        if serve_modbus:
            endpoint = resolve_endpoint(settings.modbus, parsed.plc, cli_bind=bind, cli_port=port)
            modbus = ModbusServer(loaded.io, register_map.encoding, endpoint, register_map)
    opcua = None
    if serve_opcua or (parsed.plc.protocol == "opcua" and serve_modbus):
        from openfactory.protocols.opcua import OpcUaServer  # the opcua extra (asyncua, LGPL)

        primary = parsed.plc if parsed.plc.protocol == "opcua" else None
        cli_port = port if primary is not None else opcua_port
        endpoint = resolve_endpoint(settings.opcua, primary, cli_bind=bind, cli_port=cli_port)
        opcua = OpcUaServer(
            loaded.io,
            endpoint,
            scene_name=parsed.scene.name,
            folders={c.id: c.description for c in parsed.components},
        )
    elif parsed.plc.protocol not in ("none", "modbus_tcp", "opcua"):
        log.warning(
            "plc.protocol %s is not implemented yet; running without a PLC server",
            parsed.plc.protocol,
        )
    controller = None
    if script is not None:
        from openfactory.pyplc import ScriptPlc

        try:
            controller = ScriptPlc(script, loaded.io)
        except Exception as exc:  # noqa: BLE001 - any error loading user code is reported
            typer.echo(f"error: {script}: {type(exc).__name__}: {exc}", err=True)
            raise typer.Exit(1) from None
        log.info("scripted PLC: %s (step(io, t) every tick)", script)
        if modbus is not None:
            log.warning(
                "--script writes the PLC outputs every tick; a PLC connected over Modbus would "
                "fight it. Use the Modbus server for reading (an HMI) only."
            )
    runtime = Runtime(loaded, realtime=not fast, controller=controller)
    runtime.scheduled_faults = list(faults or [])
    runtime.comms = modbus.comms if modbus is not None else None  # PLAN 2.13
    if modbus is None and any(f[1] == "comms" for f in runtime.scheduled_faults):
        typer.echo("error: --fault comms=...: this run has no protocol server", err=True)
        raise typer.Exit(1)
    web = None
    if not headless:
        web_endpoint = Endpoint(bind=bind or settings.web.bind, port=web_port or settings.web.port)
        token = fault_token or settings.server.fault_token
        app = create_app(runtime, bind=web_endpoint.bind, fault_token=token)
        web = uvicorn.Server(server_config(app, web_endpoint))
        log.info("viewer: http://%s:%d/", web_endpoint.bind, web_endpoint.port)
        if notice := fault_api_notice(web_endpoint.bind, token):
            log.warning(notice)
    if record is not None:
        from openfactory.record import FRAME_EVERY, Recorder

        header = {
            "openfactory": __version__,
            "scene_yaml": scene.read_text(encoding="utf-8"),
            "scene": runtime.scene_document(),
            "dt": runtime.scheduler.dt,
            "encoding": encoding or parsed.plc.analog_encoding,
            "frame_every": FRAME_EVERY,
        }
        runtime.recorder = Recorder(record, header, runtime.snapshot)
        log.info("recording to %s", record)
    wall = time.perf_counter()
    try:
        servers = [s for s in (modbus, opcua) if s is not None]
        asyncio.run(_serve(runtime, servers, web, duration))
    finally:
        if runtime.recorder is not None:  # Ctrl+C or a script fault still leaves a whole file
            runtime.recorder.close(_end_record(loaded, runtime))
    if runtime.fault is not None:
        typer.echo(
            f"error: --script {script} faulted at t = {loaded.time:.2f} s:\n{runtime.fault}",
            err=True,
        )
        raise typer.Exit(1)
    return runtime, time.perf_counter() - wall


def _faults(
    loaded: LoadedScene, items: list[str], at: float
) -> list[tuple[float, str, str, dict[str, Any]]]:
    """Parse ``--fault ID=KIND[:key=value,...]`` and check each against its component now, so a
    typo fails before the run instead of in the middle of it."""
    out = []
    for item in items:
        target, sep, rest = item.partition("=")
        kind, _, raw = rest.partition(":")
        if not sep or not kind:
            raise typer.BadParameter(
                f"{item!r}: expected ID=KIND[:key=value,...]", param_hint="--fault"
            )
        if target == "comms":  # PLAN 2.13: the protocol server, not a component
            out.append((at, "comms", kind, _comms_params(item, kind, raw)))
            continue
        comp = next((c for c in loaded.components if c.id == target), None)
        if comp is None:
            raise typer.BadParameter(f"unknown component {target!r}", param_hint="--fault")
        params: dict[str, Any] = {}
        for pair in filter(None, raw.split(",")):
            key, eq, value = pair.partition("=")
            if not eq:
                raise typer.BadParameter(f"{item!r}: parameter {pair!r} is not key=value")
            try:
                params[key] = float(value)
            except ValueError:
                params[key] = value  # a port name
        try:
            comp.check_fault(kind, **params)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="--fault") from None
        out.append((at, target, kind, params))
    return out


def _end_record(loaded: LoadedScene, runtime) -> dict[str, Any]:  # noqa: ANN001
    """The last line of a recording: the run summary, statistics, and any script traceback."""
    return {
        "ticks": loaded.tick,
        "t": round(loaded.time, 6),
        "spawned": loaded.world.spawned,
        "removed": dict(loaded.world.removed),
        "lost": dict(loaded.world.lost),
        "by_remover": {
            c.id: dict(c.removed)  # type: ignore[attr-defined]
            for c in loaded.components
            if c.type == "part_remover"
        },
        "statistics": runtime.scene_stats.report(),
        "error": runtime.fault,
    }


def _comms_params(item: str, kind: str, raw: str) -> dict[str, Any]:
    """``--fault comms=drop`` or ``--fault comms=latency:ms=N``, as set_comms arguments."""
    if kind == "drop" and not raw:
        return {"drop": True}
    key, _, value = raw.partition("=")
    if kind == "latency" and key == "ms":
        try:
            if float(value) > 0:
                return {"latency_ms": float(value)}
        except ValueError:
            pass
    raise typer.BadParameter(
        f"{item!r}: a comms fault is comms=drop or comms=latency:ms=N (N > 0)", param_hint="--fault"
    )


def _statistics(runtime) -> None:  # noqa: ANN001 - Runtime is imported lazily
    """The statistics block (PLAN 2.11), printed just before the run summary."""
    from openfactory.core.stats import format_report

    typer.echo(format_report(runtime.statistics()))


def _summary(loaded: LoadedScene, wall: float) -> None:
    """The one-line run summary, the last line ``run`` and ``test`` print."""
    world = loaded.world
    removers = {
        c.id: dict(c.removed)  # type: ignore[attr-defined]
        for c in loaded.components
        if c.type == "part_remover"
    }
    log.info(
        "ran %d ticks (%.2f s simulated) in %.2f s wall; spawned %d, removed %s, lost %s; "
        "by remover %s",
        loaded.tick,
        loaded.time,
        wall,
        world.spawned,
        dict(world.removed),
        dict(world.lost),
        removers,
    )


@app.command()
def replay(
    recording: Annotated[
        Path, typer.Argument(help="A recording made with --record.", metavar="FILE.ofrec")
    ],
    speed: Annotated[
        float, typer.Option(help="Playback speed; 2 is twice as fast.", min=0.01)
    ] = 1.0,
    io: Annotated[
        bool, typer.Option("--io", help="Print the I/O change log (time, tick, tag, value).")
    ] = False,
    loop: Annotated[bool, typer.Option(help="Start again at the end.")] = False,
    exit_at_end: Annotated[
        bool, typer.Option("--exit-at-end", help="Stop the server when playback ends.")
    ] = False,
    bind: Annotated[
        str | None, typer.Option(help="Listen address for the viewer (default: loopback).")
    ] = None,
    web_port: Annotated[int | None, typer.Option(help="Viewer port (default 8000).")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Warnings only.")] = False,
) -> None:
    """Replay a recording in the viewer with no simulation (PLAN 2.12)."""
    from openfactory.record import ReplayRuntime, io_table, read_recording

    _setup_logging(quiet)
    try:
        rec = read_recording(recording)
    except (OSError, ValueError) as exc:
        typer.echo(f"error: {recording}: {exc}", err=True)
        raise typer.Exit(1) from None
    if rec.truncated:
        log.warning("%s was cut short: replaying its %d frames", recording, len(rec.frames))
    if io:
        typer.echo(io_table(rec))
        return
    from openfactory.server.app import create_app, server_config

    runtime = ReplayRuntime(rec, speed=speed, loop=loop)
    settings = load_settings()
    endpoint = Endpoint(bind=bind or settings.web.bind, port=web_port or settings.web.port)
    web = uvicorn.Server(server_config(create_app(runtime, bind=endpoint.bind), endpoint))
    last = rec.frames[-1][0]
    log.info(
        "replaying %s: %s, %d ticks (%.2f s simulated) at %gx; viewer: http://%s:%d/",
        recording, rec.header["scene"]["scene"]["name"], last, last * rec.dt, speed,
        endpoint.bind, endpoint.port,
    )  # fmt: skip

    async def serve() -> None:
        task = asyncio.create_task(web.serve())
        runtime.start()
        try:
            while not task.done():
                if exit_at_end and runtime.finished:
                    web.should_exit = True
                await asyncio.sleep(0.1)
        finally:
            runtime.stop()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve())


def _scene_files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out += sorted(p for p in path.glob("*.yaml") if not p.name.endswith(LOCKFILE_SUFFIX))
        else:
            out.append(path)
    return out


@app.command()
def validate(
    scenes: Annotated[
        list[Path],
        typer.Argument(help="Scene files or directories of scenes.", metavar="SCENE..."),
    ],
) -> None:
    """Validate scenes: schema, components, warnings, and Modbus addresses. Exit 1 on failure."""
    _setup_logging(quiet=True)
    failed = 0
    files = _scene_files(scenes)
    for path in files:
        try:
            parsed, loaded = _load(path)
            # The scene's own encoding, plus any other encoding that has a lockfile (ADR-0004).
            encodings = [parsed.plc.analog_encoding] + [
                e
                for e in ENCODINGS
                if e != parsed.plc.analog_encoding and lockfile_path(path, e).is_file()  # type: ignore[arg-type]
            ]
            for enc in encodings:
                scene_register_map(parsed, enc, scene_path=path, write=False)  # type: ignore[arg-type]
        except (OSError, ValueError, ValidationError) as exc:
            failed += 1
            typer.echo(f"FAIL {path}\n{_indent(str(exc))}")
            continue
        typer.echo(
            f"OK   {path}: {len(loaded.components)} components, {len(loaded.io)} tags, "
            f"Modbus {', '.join(encodings)}"
        )
        for warning in loaded.warnings:
            typer.echo(f"     warning: {warning}")
        own = lockfile_path(path, parsed.plc.analog_encoding)
        if parsed.plc.protocol == "modbus_tcp" and not own.is_file():
            typer.echo(f"     note: no {own.name} yet; 'run' or 'export' writes it")
    if not files:
        typer.echo("no scenes given")
        raise typer.Exit(1)
    if failed:
        typer.echo(f"{failed} of {len(files)} scene(s) failed")
        raise typer.Exit(1)


@app.command()
def lint(
    scenes: Annotated[
        list[Path],
        typer.Argument(help="Scene files or directories of scenes.", metavar="SCENE..."),
    ],
    script: Annotated[
        list[Path] | None,
        typer.Option(help="Stand-in PLC script(s) whose io[...] writes and reads count (2.10)."),
    ] = None,
) -> None:
    """Lint scenes for likely mistakes (PLAN 2.20). Exit 1 on errors, 0 with only warnings."""
    from openfactory.lint import lint_scene

    _setup_logging(quiet=True)
    files = _scene_files(scenes)
    if not files:
        typer.echo("no scenes given")
        raise typer.Exit(1)
    errors = warnings = 0
    for path in files:
        findings = lint_scene(path, script or [])
        errors += sum(f.level == "error" for f in findings)
        warnings += sum(f.level == "warning" for f in findings)
        typer.echo(f"{'FAIL' if any(f.level == 'error' for f in findings) else 'OK  '} {path}")
        for finding in findings:
            typer.echo(_indent(str(finding)))
    typer.echo(f"{len(files)} scene(s): {errors} error(s), {warnings} warning(s)")
    if errors:
        raise typer.Exit(1)


def _indent(text: str) -> str:
    return "\n".join(f"     {line}" for line in text.splitlines())


@app.command()
def export(
    scene: Annotated[Path, typer.Argument(help="Scene file.", metavar="SCENE")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output file (default: stdout).")
    ] = None,
    analog_encoding: AnalogEncoding = None,
    format_: Annotated[
        ExportFormat,
        typer.Option(
            "--format",
            help="csv: the I/O list with Modbus addresses. l5x: Studio 5000 controller tag "
            "stubs, one per I/O list row (PLAN 2.3). ignition: Ignition 8.3 tag JSON, a UDT "
            "instance per component (PLAN 2.4).",
        ),
    ] = "csv",
    controller: Annotated[
        str, typer.Option(help="l5x: the controller name in TargetName and the Controller element.")
    ] = "OpenFactory",
    prefix: Annotated[
        str | None,
        typer.Option(
            help="l5x: an area prefix, e.g. AREA1, prepended to every tag name (AREA1_PE_01_DI) "
            "and description.",
        ),
    ] = None,
    area: Annotated[
        str | None,
        typer.Option(help="ignition: the area folder (default: the scene name in capitals)."),
    ] = None,
    opc_server: Annotated[
        str,
        typer.Option(help="ignition: the gateway's OPC connection to OpenFactory's OPC UA server."),
    ] = "OpenFactory",
    device: Annotated[
        str | None,
        typer.Option(
            help="ignition: address a Logix device through Ignition's driver instead "
            "(ns=1;s=[DEVICE]TAG); the device alias from the gateway, never an IP.",
        ),
    ] = None,
) -> None:
    """Export the I/O list CSV, L5X tag stubs, or Ignition tag JSON (updates the lockfile)."""
    from openfactory.export.ignition_tags import ignition_tags_json
    from openfactory.export.io_list_csv import io_list_csv
    from openfactory.export.l5x_stub import l5x_stub

    if format_ not in EXPORT_FORMATS:
        raise typer.BadParameter(
            f"{format_!r}: use one of {', '.join(EXPORT_FORMATS)}", param_hint="--format"
        )
    encoding = _encoding(analog_encoding)
    try:
        parsed, _ = _load(scene)
        if format_ == "l5x":
            text = l5x_stub(
                parsed, encoding, scene_path=scene, controller=controller, prefix=prefix
            )
        elif format_ == "ignition":
            scene_register_map(parsed, encoding, scene_path=scene)  # lockfile, as for csv
            text = ignition_tags_json(parsed, area=area, opc_server=opc_server, device=device)
        else:
            text = io_list_csv(parsed, encoding, scene_path=scene)
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"error: {scene}: {exc}", err=True)
        raise typer.Exit(1) from None
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
