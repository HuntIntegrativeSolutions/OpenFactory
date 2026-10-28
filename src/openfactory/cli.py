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
- ``run`` and ``test`` take ``--fault ID=KIND[:key=value,...]`` (repeatable) and ``--fault-at T``
  (simulated seconds, default 0): each fault is checked against its component before the run and
  injected between ticks once the simulated time reaches T, logged like an ``/api/fault`` request,
  so a headless run or a graded exercise can include a jammed pusher or a stuck eye (PLAN 2.11).
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
    modbus: ModbusServer | None,
    web: uvicorn.Server | None,
    duration: float | None,
) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):  # Windows
            loop.add_signal_handler(sig, stop.set)
    tasks = []
    if modbus is not None:
        tasks.append(asyncio.create_task(modbus.serve_forever()))
        await modbus.wait_ready()
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
        if modbus is not None:
            await modbus.shutdown()
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
    fault: Annotated[
        list[str] | None,
        typer.Option(
            help="ID=KIND[:key=value,...]: inject a fault (PLAN 2.6) at --fault-at; repeatable.",
        ),
    ] = None,
    fault_at: Annotated[
        float, typer.Option(help="Simulated time, seconds, to inject the --fault faults.", min=0)
    ] = 0.0,
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
        faults=faults,
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
    faults: list[tuple[float, str, str, dict[str, Any]]] | None = None,
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
    elif parsed.plc.protocol != "none":
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
    web = None
    if not headless:
        web_endpoint = Endpoint(bind=bind or settings.web.bind, port=web_port or settings.web.port)
        token = fault_token or settings.server.fault_token
        app = create_app(runtime, bind=web_endpoint.bind, fault_token=token)
        web = uvicorn.Server(server_config(app, web_endpoint))
        log.info("viewer: http://%s:%d/", web_endpoint.bind, web_endpoint.port)
        if notice := fault_api_notice(web_endpoint.bind, token):
            log.warning(notice)
    wall = time.perf_counter()
    asyncio.run(_serve(runtime, modbus, web, duration))
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
        Path | None, typer.Option("--output", "-o", help="CSV file (default: stdout).")
    ] = None,
    analog_encoding: AnalogEncoding = None,
) -> None:
    """Export the I/O list CSV with Modbus addresses (updates the address lockfile)."""
    from openfactory.export.io_list_csv import io_list_csv

    encoding = _encoding(analog_encoding)
    try:
        parsed, _ = _load(scene)
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
