"""``openfactory`` command line (PLAN task 1.10): ``run``, ``test``, ``validate``, ``lint``,
``export``, ``bundle``, ``edit`` (PLAN 4.1: the scene editor, paused, loopback only;
:mod:`openfactory.server.editor`), ``migrate`` (PLAN 4.7: rewrites scenes in an older format
version to the current one in place, keeping comments and key order; ``--check`` writes nothing
and exits 1 if a file needs it; :mod:`openfactory.core.migrate`).

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
- ``run --logix NAME`` (PLAN 2.2, ADR-0002): exchange the I/O table with a Logix controller at
  the named connection in ``openfactory.toml`` instead of serving Modbus
  (:mod:`openfactory.protocols.logix`); a scene's ``plc: {protocol: logix, role: client,
  connection: NAME}`` does the same.
- ``run --exam`` (PLAN 2.11): every DI bound with ``exam: switch`` follows an operator switch
  in the viewer (``/api/exam``) instead of the simulation, starting off, and state frames carry
  no fault markers, so trainees find faults from the PLC's side.
- ``run`` and ``test`` take ``--record FILE.ofrec`` (PLAN 2.12, :mod:`openfactory.record`): the
  run's I/O changes, state frames and events, with the scene itself, so ``openfactory replay
  FILE.ofrec [--speed X] [--loop] [--exit-at-end]`` can show it in the viewer with no scene file and
  no simulation, and ``openfactory replay FILE.ofrec --io`` prints the I/O change log.
- ``look`` (PLAN 5.14b, :mod:`openfactory.observe`) prints the plant in words, as Markdown: from a
  running server (``GET /api/look``, ``--url``, default the ``[web]`` endpoint) or from a recording
  at a simulated time (``openfactory look FILE.ofrec --at T``, the frame at or before T; the end
  without ``--at``), with ``--since S`` and ``--component ID``.
- ``replay --paused`` starts paused at the first frame; the viewer's replay bar plays, pauses,
  seeks and steps (PLAN 5.14f). ``strip FILE.ofrec|CAPTURE --at T --span S --frames N
  [--look-at ID] [--labels tags]`` (:mod:`openfactory.strip`) writes a film strip: N stills in
  a row, each stamped with its time, and a table of what changed between them.
- ``explain [FILE.ofrec|CAPTURE] [--check NAME | --part ID] [--json]`` (PLAN 5.14g,
  :mod:`openfactory.explain`): why it happened, from a recording, a capture folder, or the
  running server's last 60 s (``POST /api/explain``).
- ``captures`` (PLAN 5.14e, :mod:`openfactory.capture`) lists the viewer's captures, newest
  first, and prints the newest folder's path last; ``run --capture-dir DIR`` sets where the
  viewer's Capture writes (default ``openfactory-captures/`` where ``run`` was started).
- ``run`` and ``test`` take ``--fault ID=KIND[:key=value,...]`` (repeatable) and ``--fault-at T``
  (simulated seconds, default 0): each fault is checked against its component before the run and
  injected between ticks once the simulated time reaches T, logged like an ``/api/fault`` request,
  so a headless run or a graded exercise can include a jammed pusher or a stuck eye (PLAN 2.11).
  ``--fault comms=drop`` or ``--fault comms=latency:ms=N`` stalls or delays the protocol servers
  instead, Modbus and OPC UA alike (PLAN 2.13, 2.13b); the simulation keeps running.
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
- ``run`` and ``export`` rewrite ``<scene>.CONTEXT.md`` beside a scene file when it changed
  (PLAN 2.19, :mod:`openfactory.export.context_md`); a failure to write it is a warning. Nothing
  is written for a ``.ofz`` bundle.
- ``openfactory bundle SCENE [--out F.ofz] [--script F.py | --no-script]`` (PLAN 2.18,
  :mod:`openfactory.bundle`) writes one ``.ofz`` with the scene, its meshes and their licenses,
  both lockfiles and the stand-in script. ``run``, ``test`` and ``validate`` take a ``.ofz`` in
  place of SCENE: it is checked, unpacked into a temporary folder for the command, and its script
  runs only with ``--bundled-script``.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, get_args

import typer
import uvicorn
from pydantic import ValidationError

from openfactory import __version__
from openfactory.bundle import BundleError, collect, is_bundle, open_bundle, write_bundle
from openfactory.core.config import Endpoint, load_settings, resolve_endpoint
from openfactory.core.iotable import Writer
from openfactory.core.loader import LoadedScene, build
from openfactory.core.schema import PLC_WRITES, Scene, load_scene, read_scene_text
from openfactory.protocols.comms import CommsFault
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

EXPORT_FORMATS = ("csv", "l5x", "ignition", "plantpax", "plantpax-udts", "plantpax-faceplates")
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


@contextlib.contextmanager
def _loader_quiet() -> Iterator[None]:
    """Silence the loader's own warning log lines for a caller that prints them itself."""
    logger = logging.getLogger("openfactory.core.loader")
    level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(level)


AreaOption = Annotated[
    str | None,
    typer.Option(
        "--area",
        metavar="NAME",
        help="With a plant file (include:, PLAN 5.4): only this area, at the plant's Modbus "
        "addresses (its lockfile is read, never written), with the area's own checks.",
    ),
]


def _is_plant(path: Path) -> bool:
    """Whether ``path`` is a plant file (``include:`` at its top level, PLAN 5.4)."""
    import yaml

    try:
        data = yaml.safe_load(read_scene_text(path))
    except (OSError, ValueError, yaml.YAMLError):
        return False  # _load reports it
    return isinstance(data, dict) and data.get("include") is not None


def _load(path: Path, area: str | None = None) -> tuple[Scene, LoadedScene]:
    if path.name.endswith(LOCKFILE_SUFFIX):
        raise ValueError(f"{path} is a Modbus address lockfile, not a scene")
    scene = load_scene(path, area=area)
    for note in scene.plant.notes if scene.plant is not None else []:
        log.warning("%s", note)  # once per run (PLAN 5.4b)
    return scene, build(scene, base_dir=path.parent)


BundledScript = Annotated[
    bool,
    typer.Option(
        "--bundled-script",
        help="With a .ofz bundle: run the stand-in PLC script it carries (PLAN 2.18). It is "
        "Python, so it never runs unless asked for.",
    ),
]


def _scene_source(
    ctx: typer.Context, scene: Path, bundled_script: bool
) -> tuple[Path, Path | None]:
    """The scene file to load and, with ``--bundled-script``, the bundle's script. A ``.ofz`` is
    checked and unpacked into a temporary folder removed when the command ends (PLAN 2.18)."""
    if not is_bundle(scene):
        if bundled_script:
            raise typer.BadParameter(f"{scene} is not a .ofz bundle", param_hint="--bundled-script")
        return scene, None
    stack = contextlib.ExitStack()
    ctx.call_on_close(stack.close)
    try:
        opened = stack.enter_context(open_bundle(scene))
    except BundleError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    carried = opened.manifest.get("script")
    log.info("bundle %s: scene %s (unpacked to %s)", scene, opened.manifest["scene"],
             opened.scene_path.parent)  # fmt: skip
    if bundled_script and opened.script is None:
        typer.echo(f"error: {scene}: the bundle carries no script", err=True)
        raise typer.Exit(1)
    if carried and not bundled_script:
        log.info("the bundle carries a stand-in PLC script (%s); it runs only with "
                 "--bundled-script", carried)  # fmt: skip
    return opened.scene_path, opened.script if bundled_script else None


def _write_context(parsed: Scene, scene: Path) -> None:
    """Rewrite ``<scene>.CONTEXT.md`` beside the scene if it changed (PLAN 2.19). A failure is a
    warning: the run or export itself goes on (a lockfile conflict is reported by it)."""
    from openfactory.export.context_md import write_context

    if parsed.plant is not None and parsed.plant.area is not None:
        return  # one area of a plant: the plant's context file is not this run's (PLAN 5.4b)
    try:
        written = write_context(parsed, scene)
    except (OSError, ValueError) as exc:
        log.warning("scene context not written: %s", exc)
        return
    if written is not None:
        log.info("wrote scene context %s", written)


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
    # faults due at t = 0 are in force before any client can connect, not from the first tick
    runtime.apply_due_faults()
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
    ctx: typer.Context,
    scene: Annotated[
        Path,
        typer.Argument(help="Scene file (scenes/<name>.yaml) or .ofz bundle.", metavar="SCENE"),
    ],
    area: AreaOption = None,
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
    sparkplug: Annotated[
        bool,
        typer.Option(
            "--sparkplug",
            help="Also publish the I/O table as a Sparkplug B edge node to the MQTT broker in "
            "openfactory.toml [sparkplug] (PLAN 3.5; read-only telemetry, e.g. for Ignition's "
            "MQTT Engine).",
        ),
    ] = False,
    logix: Annotated[
        str | None,
        typer.Option(
            "--logix",
            metavar="CONNECTION",
            help="Exchange the I/O table with a Logix controller (bench, Echo or Emulate) at "
            "this named connection in openfactory.toml [connections.NAME], instead of serving "
            "Modbus (PLAN 2.2, ADR-0002; docs/logix.md). Overrides the scene's plc block.",
        ),
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
    bundled_script: BundledScript = False,
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
    capture_dir: Annotated[
        Path | None,
        typer.Option(
            help="Where the viewer's Capture writes (PLAN 5.14e; default: openfactory-captures/ "
            "in the folder `run` is started from).",
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
    if bundled_script and script is not None:
        raise typer.BadParameter("give --script or --bundled-script, not both")
    from_file = not is_bundle(scene)
    scene, carried = _scene_source(ctx, scene, bundled_script)
    script = carried or script
    try:
        parsed, loaded = _load(scene, area)
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"error: {scene}: {exc}", err=True)
        raise typer.Exit(1) from None
    log.info("OpenFactory %s: %s (training and demo tool, not safety-rated)", __version__, scene)
    if from_file:
        _write_context(parsed, scene)

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
        sparkplug=sparkplug,
        logix=logix,
        faults=faults,
        record=record,
        capture_dir=capture_dir,
    )
    _statistics(runtime)
    _summary(loaded, wall)


@app.command()
def test(
    ctx: typer.Context,
    scene: Annotated[
        Path,
        typer.Argument(help="Scene file or .ofz bundle with a checks: block.", metavar="SCENE"),
    ],
    area: AreaOption = None,
    script: Annotated[
        Path | None,
        typer.Option(help="Grade a stand-in PLC script (step(io, t)); runs as fast as possible."),
    ] = None,
    bundled_script: BundledScript = False,
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
    if (script is not None) + bundled_script + plc != 1:
        raise typer.BadParameter("give exactly one of --script FILE.py, --bundled-script or --plc")
    encoding = _encoding(analog_encoding)
    scene, carried = _scene_source(ctx, scene, bundled_script)
    script = carried or script
    try:
        parsed, loaded = _load(scene, area)
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
    sparkplug: bool = False,
    logix: str | None = None,
    faults: list[tuple[float, str, str, dict[str, Any]]] | None = None,
    record: Path | None = None,
    capture_dir: Path | None = None,
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
    logix_client = None
    connection = logix or (parsed.plc.connection if parsed.plc.protocol == "logix" else None)
    if logix is not None or parsed.plc.protocol == "logix":
        logix_client = _logix_client(parsed, loaded, settings, connection, script)
    modbus = None
    if logix_client is None and parsed.plc.protocol == "modbus_tcp":
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
    elif parsed.plc.protocol not in ("none", "modbus_tcp", "opcua", "logix"):
        log.warning(
            "plc.protocol %s is not implemented yet; running without a PLC server",
            parsed.plc.protocol,
        )
    publisher = None
    if sparkplug:
        from openfactory.protocols.sparkplug import SparkplugEndpoint, SparkplugPublisher

        cfg = settings.sparkplug
        try:
            publisher = SparkplugPublisher(
                loaded.io,
                SparkplugEndpoint(cfg.broker, cfg.port, cfg.group),
                cfg.node or parsed.scene.name,
            )
        except ValueError as exc:
            typer.echo(f"error: --sparkplug: {exc}", err=True)
            raise typer.Exit(1) from None
        log.info("sparkplug: %s on %s:%d", publisher.topic("NBIRTH"), cfg.broker, cfg.port)
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
    # PLAN 2.13, 2.13b: one comms fault for every protocol server in the run
    servers_with_comms = [s for s in (modbus, opcua) if s is not None]
    runtime.comms = CommsFault() if servers_with_comms else None
    for server in servers_with_comms:
        server.comms = runtime.comms
    runtime.protocols = [name for name, s in (("Modbus TCP", modbus), ("OPC UA", opcua)) if s]
    if not servers_with_comms and any(f[1] == "comms" for f in runtime.scheduled_faults):
        typer.echo("error: --fault comms=...: this run has no protocol server", err=True)
        raise typer.Exit(1)
    web = None
    if not headless:
        web_endpoint = Endpoint(bind=bind or settings.web.bind, port=web_port or settings.web.port)
        token = fault_token or settings.server.fault_token
        from openfactory.capture import DEFAULT_DIR

        captures = (capture_dir or Path.cwd() / DEFAULT_DIR).resolve()  # ruling 2: beside `run`
        app = create_app(runtime, bind=web_endpoint.bind, fault_token=token, capture_dir=captures)
        web = uvicorn.Server(server_config(app, web_endpoint))
        log.info("viewer: http://%s:%d/", web_endpoint.bind, web_endpoint.port)
        if notice := fault_api_notice(web_endpoint.bind, token):
            log.warning(notice)
    if record is not None or web is not None:
        from openfactory.record import recording_header

        # a recording's header, and a capture's piece of recording (PLAN 5.14e)
        header = recording_header(runtime, scene.read_text(encoding="utf-8"), encoding)
        runtime.recording_header = header
    if web is None:
        runtime.capture_buffer = None  # no viewer, so nothing can capture (PLAN 5.14e)
    if record is not None:
        from openfactory.record import Recorder

        runtime.recorder = Recorder(record, header, runtime.snapshot)
        log.info("recording to %s", record)
    wall = time.perf_counter()
    try:
        servers = [s for s in (modbus, opcua, publisher, logix_client) if s is not None]
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


def _logix_client(parsed: Scene, loaded: LoadedScene, settings, name, script):  # noqa: ANN001, ANN202
    """The Logix client for a named connection (PLAN 2.2, ADR-0002), or exit 1 saying why."""
    from openfactory.protocols.logix import LogixClient, plant_names

    def fail(message: str) -> None:
        typer.echo(f"error: logix: {message}", err=True)
        raise typer.Exit(1)

    if parsed.plc.protocol == "logix" and parsed.plc.role != "client":
        fail("plc.role must be client: OpenFactory polls the controller (PLAN 6.2)")
    if not name:
        fail("no connection named: set plc.connection in the scene or pass --logix NAME")
    conn = settings.connections.get(name)
    if conn is None:
        known = ", ".join(sorted(settings.connections)) or "none"
        fail(
            f"no [connections.{name}] in openfactory.toml or openfactory.local.toml "
            f"(connections: {known})"
        )
    if conn.protocol != "logix":
        fail(f"[connections.{name}] has protocol {conn.protocol!r}, not 'logix'")
    if script is not None:
        fail("--script and a Logix controller would both write the PLC outputs; use one")
    log.info("logix: connection %s, CIP path %s", name, conn.path)
    log.info(
        "logix: on link loss, outputs %s",
        f"off after {conn.link_loss_timeout_s:g} s" if conn.on_link_loss == "off" else "held",
    )
    plant = plant_names(parsed)  # PLAN 5.3b: exchanged by the plant's names when it has them
    if plant:
        log.info("logix: %d points by their plant names (plc_tag)", len(plant))
    return LogixClient(
        loaded.io,
        conn.path,
        on_link_loss=conn.on_link_loss,
        link_loss_timeout_s=conn.link_loss_timeout_s,
        plant=plant,
    )


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
    paused: Annotated[
        bool,
        typer.Option(
            "--paused", help="Start paused at the first frame (PLAN 5.14f: play, seek, step)."
        ),
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

    runtime = ReplayRuntime(rec, speed=speed, loop=loop, paused=paused)
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


@app.command()
def strip(
    source: Annotated[
        Path,
        typer.Argument(
            help="A recording, or a capture folder (its last-60s.ofrec).",
            metavar="FILE.ofrec|CAPTURE",
        ),
    ],
    at: Annotated[
        float | None,
        typer.Option(help="Simulated time the stills are centred on, s (default: the end)."),
    ] = None,
    span: Annotated[float, typer.Option(help="Seconds the stills cover.", min=0.01)] = 2.0,
    frames: Annotated[int, typer.Option(help="How many stills.", min=2, max=24)] = 6,
    look_at: Annotated[
        str | None, typer.Option(help="Frame this component and what touches it (?look=).")
    ] = None,
    labels: Annotated[
        str, typer.Option(help="Labels: ids, tags (with values) or off (PLAN 5.14d).")
    ] = "ids",
    size: Annotated[str, typer.Option(help="The viewer's size, WIDTHxHEIGHT; stills are half.")] = (
        "960x540"
    ),
    out: Annotated[
        Path | None,
        typer.Option(help="The image (default: <recording>-strip-<T>s.png here); .md beside it."),
    ] = None,
) -> None:
    """A film strip of a recording (PLAN 5.14f): stills in a row, each stamped with its time,
    and a table of what changed between them (printed, and written beside the image)."""
    from openfactory.strip import make_strip, recording_of

    if labels not in ("ids", "tags", "off"):
        raise typer.BadParameter("ids, tags or off", param_hint="--labels")
    try:
        width, height = (int(v) for v in size.lower().split("x"))
    except ValueError:
        raise typer.BadParameter("WIDTHxHEIGHT, e.g. 960x540", param_hint="--size") from None
    try:
        name = recording_of(source).stem if source.is_dir() else source.stem
        if source.is_dir():
            name = f"{source.parent.name}-{source.name}"
        target = out or Path(f"{name}-strip-{'end' if at is None else f'{at:g}'}s.png")
        result = make_strip(
            source, out=target, at_s=at, span_s=span, frames=frames, look_at=look_at,
            labels=labels, size=(width, height),
        )  # fmt: skip
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"error: {source}: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(result["table"], nl=False)
    typer.echo(f"wrote {target} and {target.with_suffix('.md')}")


@app.command()
def captures(
    capture_dir: Annotated[
        Path | None,
        typer.Option(
            help="The capture folder (default: openfactory-captures/ in this folder).",
            show_default=False,
        ),
    ] = None,
) -> None:
    """List the viewer's captures (PLAN 5.14e), newest first; the last line is the newest
    folder's path."""
    from openfactory.capture import DEFAULT_DIR, list_captures

    base = capture_dir or Path(DEFAULT_DIR)
    found = list_captures(base)
    if not found:
        typer.echo(f"no captures in {base}")
        raise typer.Exit(1)
    for folder in found:
        lines = (folder / "report.md").read_text(encoding="utf-8").splitlines()
        title = lines[0].removeprefix("# Capture: ") if lines else folder.name
        note = next((ln[2:] for ln in lines if ln.startswith("> ")), "")
        typer.echo(f"{folder}  {title}" + (f"  {note[:60]}" if note else ""))
    typer.echo(str(found[0]))


@app.command()
def look(
    recording: Annotated[
        Path | None,
        typer.Argument(
            help="A recording to look into (--at); without one, the running server.",
            metavar="[FILE.ofrec]",
        ),
    ] = None,
    at: Annotated[
        float | None,
        typer.Option(help="Simulated time in the recording, s: the frame at or before it."),
    ] = None,
    url: Annotated[
        str | None,
        typer.Option(help="The running server (default: the [web] endpoint, 127.0.0.1:8000)."),
    ] = None,
    since: Annotated[
        float, typer.Option(help="Show the changes of the last S simulated seconds.", min=0.01)
    ] = 5.0,
    component: Annotated[
        str | None, typer.Option(help="Narrow the report to one component, by id.")
    ] = None,
) -> None:
    """The plant in words (PLAN 5.14b): components, tags, parts, checks and recent changes, as
    Markdown, from a running scene or from a recording at a time."""
    if recording is not None:
        from openfactory.observe import look_recording
        from openfactory.record import read_recording

        try:
            rec = read_recording(recording)
        except (OSError, ValueError) as exc:
            typer.echo(f"error: {recording}: {exc}", err=True)
            raise typer.Exit(1) from None
        last = rec.frames[-1][0] * rec.dt
        when = last if at is None else at
        try:
            typer.echo(look_recording(rec, when, since_s=since, component=component), nl=False)
        except KeyError as exc:
            typer.echo(f"error: {exc.args[0]}", err=True)
            raise typer.Exit(1) from None
        return
    if at is not None:
        typer.echo("error: --at needs a recording (FILE.ofrec)", err=True)
        raise typer.Exit(2)
    import urllib.error
    import urllib.parse
    import urllib.request

    if url is None:
        web = load_settings().web
        url = f"http://{'127.0.0.1' if web.bind in ('0.0.0.0', '::') else web.bind}:{web.port}"
    query = {"since": f"{since:g}", **({"component": component} if component else {})}
    target = f"{url.rstrip('/')}/api/look?{urllib.parse.urlencode(query)}"
    try:
        with urllib.request.urlopen(target, timeout=10) as response:  # noqa: S310 - our server
            typer.echo(response.read().decode("utf-8"), nl=False)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        typer.echo(f"error: {target}: {exc.code} {detail}", err=True)
        raise typer.Exit(1) from None
    except (urllib.error.URLError, OSError) as exc:
        typer.echo(f"error: {url}: no running OpenFactory server ({exc})", err=True)
        raise typer.Exit(1) from None


@app.command()
def explain(
    recording: Annotated[
        Path | None,
        typer.Argument(
            help="A recording, or a capture folder (its last-60s.ofrec); without one, the "
            "running server's last 60 s.",
            metavar="[FILE.ofrec|CAPTURE]",
        ),
    ] = None,
    check: Annotated[
        str | None, typer.Option(help="A check by name (or its start, e.g. parts_reached).")
    ] = None,
    part: Annotated[str | None, typer.Option(help="A part by id, e.g. P000002.")] = None,
    url: Annotated[
        str | None,
        typer.Option(help="The running server (default: the [web] endpoint, 127.0.0.1:8000)."),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the steps and the trail as JSON.")
    ] = False,
) -> None:
    """Why it happened (PLAN 5.14g): a part's story, or a failed check's, in order with times,
    each line naming the tick and the data it rests on. With neither --check nor --part, every
    failed check."""
    import json

    if check is not None and part is not None:
        typer.echo("error: give --check or --part, not both", err=True)
        raise typer.Exit(2)
    if recording is not None:
        from openfactory.explain import Unknown
        from openfactory.explain import explain as explain_recording
        from openfactory.record import read_recording
        from openfactory.strip import recording_of

        try:
            rec = read_recording(recording_of(recording))
            answer = explain_recording(rec, check=check, part=part).document()
        except Unknown as exc:
            typer.echo(f"error: {exc.args[0]}", err=True)
            raise typer.Exit(1) from None
        except (OSError, ValueError) as exc:
            typer.echo(f"error: {recording}: {exc}", err=True)
            raise typer.Exit(1) from None
    else:
        import urllib.error
        import urllib.request

        if url is None:
            web = load_settings().web
            host = "127.0.0.1" if web.bind in ("0.0.0.0", "::") else web.bind
            url = f"http://{host}:{web.port}"
        body = {k: v for k, v in (("check", check), ("part", part)) if v is not None}
        target = f"{url.rstrip('/')}/api/explain"
        request = urllib.request.Request(
            target, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - ours
                answer = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            typer.echo(f"error: {target}: {exc.code} {detail}", err=True)
            raise typer.Exit(1) from None
        except (urllib.error.URLError, OSError) as exc:
            typer.echo(f"error: {url}: no running OpenFactory server ({exc})", err=True)
            raise typer.Exit(1) from None
    if as_json:
        typer.echo(json.dumps(answer, indent=1))
    else:
        typer.echo(answer["text"], nl=False)


def _scene_files(paths: list[Path]) -> list[Path]:
    """The scene files named, a folder as its ``*.yaml`` (no lockfiles). A file that a plant in
    the same folder includes is left out: it is checked inside its plant (PLAN 5.4c)."""
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            found = sorted(p for p in path.glob("*.yaml") if not p.name.endswith(LOCKFILE_SUFFIX))
            included = {f for p in found for f in _included(p)}
            out += [p for p in found if p.resolve() not in included]
        else:
            out.append(path)
    return out


def _included(path: Path) -> set[Path]:
    """The resolved files a plant file includes; empty for any other file."""
    import yaml

    from openfactory.core.plant import PlantError, include_path

    if not _is_plant(path):
        return set()
    data = yaml.safe_load(read_scene_text(path))
    out = set()
    for entry in data.get("include") or []:
        try:
            out.add(include_path(path.resolve(), entry["file"], path.resolve().parent))
        except (PlantError, KeyError, TypeError):
            continue  # the plant's own lint reports it
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
            with contextlib.ExitStack() as stack:
                source = path
                if is_bundle(path):  # checked and unpacked for this scene only (PLAN 2.18)
                    source = stack.enter_context(open_bundle(path)).scene_path
                with _loader_quiet():  # its warnings are echoed under the scene below, once
                    parsed, loaded = _load(source)
                # The scene's own encoding, plus any other encoding with a lockfile (ADR-0004).
                encodings = [parsed.plc.analog_encoding] + [
                    e
                    for e in ENCODINGS
                    if e != parsed.plc.analog_encoding and lockfile_path(source, e).is_file()  # type: ignore[arg-type]
                ]
                for enc in encodings:
                    scene_register_map(parsed, enc, scene_path=source, write=False)  # type: ignore[arg-type]
                own = lockfile_path(source, parsed.plc.analog_encoding)
                own_missing = parsed.plc.protocol == "modbus_tcp" and not own.is_file()
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
        if own_missing:
            typer.echo(f"     note: no {own.name} yet; 'run' or 'export' writes it")
    if not files:
        typer.echo("no scenes given")
        raise typer.Exit(1)
    if failed:
        typer.echo(f"{failed} of {len(files)} scene(s) failed")
        raise typer.Exit(1)


@app.command()
def edit(
    scene: Annotated[Path, typer.Argument(help="The scene file to edit.", metavar="SCENE")],
    web_port: Annotated[int | None, typer.Option(help="Viewer port (default 8000).")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Warnings and errors only.")] = False,
    area: Annotated[
        str | None,
        typer.Option(
            "--area",
            metavar="NAME",
            help="On a plant file (include:, PLAN 5.4e): edit this area's file, with the rest of "
            "the plant drawn dimmed. Without it, the plant file's own components and its "
            "connections across areas are edited, and every area is dimmed.",
        ),
    ] = None,
) -> None:
    """Edit a scene in the browser (PLAN 4.1): the viewer in edit mode, the simulation paused,
    on loopback only, with no PLC or protocol server. Saves change only the edited lines; the
    address lockfiles are never written (the next run or export assigns new tags). On a plant
    file (PLAN 5.4e) one file is edited, the whole plant is shown, and a save lints the whole
    plant and writes only that file."""
    _setup_logging(quiet)
    from openfactory.core.loader import build
    from openfactory.server.app import create_app, server_config
    from openfactory.server.editor import EditSession
    from openfactory.server.runtime import Runtime

    if is_bundle(scene) or not scene.is_file():
        typer.echo(f"error: {scene}: not a scene file (unpack a bundle first)", err=True)
        raise typer.Exit(1)
    try:
        session = EditSession(scene, area=area)
        loaded = build(load_scene(scene), base_dir=scene.parent)
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"error: {scene}: does not load, fix it by hand first:\n{exc}", err=True)
        raise typer.Exit(1) from None
    runtime = Runtime(loaded, realtime=False)  # never started: the simulation stays paused
    settings = load_settings()
    endpoint = Endpoint(bind="127.0.0.1", port=web_port or settings.web.port)  # loopback only
    app = create_app(runtime, bind=endpoint.bind, edit=session)
    web = uvicorn.Server(server_config(app, endpoint))
    if session.plant is not None:
        log.info("plant %s: editing %s, the rest of the plant dimmed", scene.name, session.name)
    log.info("editing %s: http://%s:%d/ (Ctrl+C to stop)", scene, endpoint.bind, endpoint.port)
    with contextlib.suppress(KeyboardInterrupt):
        web.run()


@app.command()
def new(
    scene: Annotated[Path, typer.Argument(help="The scene file to create.", metavar="SCENE")],
    first: Annotated[
        str, typer.Option(help="The type of the one component it starts with.")
    ] = "belt_conveyor",
    open_editor: Annotated[
        bool, typer.Option("--edit/--no-edit", help="Open the editor on it (the default).")
    ] = True,
    web_port: Annotated[int | None, typer.Option(help="Viewer port (default 8000).")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Warnings and errors only.")] = False,
) -> None:
    """Start a new scene and open it in the editor (PLAN 4.1): one component (a belt conveyor
    unless --first says otherwise) at the origin, a placeholder scene description, a Modbus TCP
    server block. Never overwrites a file."""
    from openfactory.core.templates import new_scene

    if scene.exists():
        typer.echo(f"error: {scene} exists; `openfactory edit {scene}` opens it", err=True)
        raise typer.Exit(1)
    try:
        text = new_scene(scene, first)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    scene.parent.mkdir(parents=True, exist_ok=True)
    scene.write_text(text, encoding="utf-8")
    typer.echo(f"wrote {scene}")
    if open_editor:
        edit(scene, web_port=web_port, quiet=quiet)


@app.command()
def migrate(
    scenes: Annotated[
        list[Path],
        typer.Argument(
            help="Scene files (or library composites) or directories of them.", metavar="SCENE..."
        ),
    ],
    check: Annotated[
        bool,
        typer.Option("--check", help="Write nothing; exit 1 if any file needs migrating."),
    ] = False,
) -> None:
    """Rewrite scenes in older format versions to the current one (PLAN 4.7), in place, keeping
    comments and key order. A file already current is left untouched. Exit 1 on an error, or
    with --check when a file would change."""
    from openfactory.core.migrate import migrate_text
    from openfactory.core.schema import SCHEMA_VERSION

    files = _scene_files(scenes)
    if not files:
        typer.echo("no scenes given")
        raise typer.Exit(1)
    failed = stale = 0
    for path in files:
        if is_bundle(path):
            failed += 1
            typer.echo(f"FAIL {path}: a bundle; unpack it, migrate its scene, bundle it again")
            continue
        try:
            text = path.read_text(encoding="utf-8")
            result = migrate_text(text)
        except (OSError, ValueError) as exc:
            failed += 1
            typer.echo(f"FAIL {path}: {exc}")
            continue
        if not result.changed:
            typer.echo(f"OK   {path}: format {SCHEMA_VERSION}, current")
            continue
        stale += 1
        if check:
            typer.echo(f"OLD  {path}: format {result.source}, needs migrating to {SCHEMA_VERSION}")
            continue
        path.write_text(result.text, encoding="utf-8")
        typer.echo(f"MIGRATED {path}: " + "; ".join(result.steps))
    if failed:
        typer.echo(f"{failed} of {len(files)} file(s) failed")
    if check and stale:
        typer.echo(f"{stale} of {len(files)} file(s) need `openfactory migrate`")
    if failed or (check and stale):
        raise typer.Exit(1)


@app.command()
def bundle(
    scene: Annotated[
        Path, typer.Argument(help="Scene file (scenes/<name>.yaml).", metavar="SCENE")
    ],
    out: Annotated[
        Path | None,
        typer.Option(help="Bundle to write (default: <scene>.ofz in the current folder)."),
    ] = None,
    script: Annotated[
        Path | None,
        typer.Option(
            help="Stand-in PLC script to carry (default: <scene>.py beside the scene, else "
            "examples/pyplc/<scene>.py above it, up to the project root)."
        ),
    ] = None,
    no_script: Annotated[
        bool, typer.Option("--no-script", help="Carry no script, even if one is found.")
    ] = False,
) -> None:
    """Write a .ofz bundle: the scene, its meshes and licenses, lockfiles, and script (2.18)."""
    _setup_logging(quiet=True)
    if script is not None and no_script:
        raise typer.BadParameter("give --script or --no-script, not both")
    target = out if out is not None else Path(f"{scene.stem}.ofz")
    try:
        contents = collect(scene, script=script, no_script=no_script)
        write_bundle(contents, target)
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"error: {scene}: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"wrote {target} ({target.stat().st_size} bytes):")
    for f in sorted(contents.files, key=lambda f: f.path):
        note = f"  ({f.note})" if f.note else ""
        typer.echo(f"  {f.role:<8} {f.path}  {len(f.data)} bytes{note}")
    if contents.script is None:
        typer.echo("  no stand-in PLC script")


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
            "instance per component (PLAN 2.4). plantpax: the PlantPAx demo L5X for a scene with a "
            "PlantPAx map (PLAN 3.4; docs/plantpax.md lists what to verify on import). "
            "plantpax-udts, plantpax-faceplates: its Ignition UDTs and Perspective faceplates "
            "(PLAN 3.8; --device names the gateway's Logix device).",
        ),
    ] = "csv",
    controller: Annotated[
        str,
        typer.Option(
            help="l5x, plantpax: the controller name in TargetName and the Controller element."
        ),
    ] = "OpenFactory",
    prefix: Annotated[
        str | None,
        typer.Option(
            help="l5x: an area prefix, e.g. AREA1, prepended to every tag name (AREA1_PE_01_DI) "
            "and description, except a binding's plc_tag (default: the scene's scene.area).",
        ),
    ] = None,
    area: Annotated[
        str | None,
        typer.Option(
            help="On a plant file (include:, PLAN 5.4), it selects the area to export, at the "
            "plant's Modbus addresses (the plant's lockfile is read, never written), and that "
            "area's name is also the ignition export's area folder. On any other scene, it names "
            "the ignition export's area folder (default: scene.area, else the scene name in "
            "capitals).",
        ),
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
    """Export the I/O list CSV, L5X tag stubs, Ignition tag JSON, or the PlantPAx demo L5X
    (updates the lockfile)."""
    from openfactory.export.ignition_tags import ignition_tags_json
    from openfactory.export.io_list_csv import io_list_csv
    from openfactory.export.l5x_stub import l5x_stub, undeclared_plc_tags
    from openfactory.export.plantpax import plantpax_l5x
    from openfactory.export.plantpax_hmi import as_json, plantpax_faceplates, plantpax_udts

    if format_ not in EXPORT_FORMATS:
        raise typer.BadParameter(
            f"{format_!r}: use one of {', '.join(EXPORT_FORMATS)}", param_hint="--format"
        )
    encoding = _encoding(analog_encoding)
    try:
        parsed, _ = _load(scene, area if _is_plant(scene) else None)
        if format_ == "l5x":
            text = l5x_stub(
                parsed, encoding, scene_path=scene, controller=controller, prefix=prefix
            )
            if undeclared := undeclared_plc_tags(parsed):  # PLAN 5.3a
                typer.echo(
                    f"{len(undeclared)} plant tags are members, elements or bits of the plant's "
                    "own tags and are not declared here",
                    err=True,
                )
        elif format_ == "plantpax":
            text = plantpax_l5x(parsed, encoding, scene_path=scene, controller=controller)
        elif format_ == "plantpax-udts":
            scene_register_map(parsed, encoding, scene_path=scene)  # lockfile, as for csv
            udts = plantpax_udts(parsed, device=device) if device else plantpax_udts(parsed)
            text = as_json(udts)
        elif format_ == "plantpax-faceplates":
            scene_register_map(parsed, encoding, scene_path=scene)
            text = as_json(plantpax_faceplates(parsed))
        elif format_ == "ignition":
            scene_register_map(parsed, encoding, scene_path=scene)  # lockfile, as for csv
            text = ignition_tags_json(parsed, area=area, opc_server=opc_server, device=device)
        else:
            text = io_list_csv(parsed, encoding, scene_path=scene)
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"error: {scene}: {exc}", err=True)
        raise typer.Exit(1) from None
    _write_context(parsed, scene)
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)


takeoff_app = typer.Typer(
    help="The takeoff file between a plant's drawings and a scene (PLAN 5.1; docs/takeoff.md).",
    no_args_is_help=True,
)
app.add_typer(takeoff_app, name="takeoff")


@takeoff_app.command("check")
def takeoff_check(
    takeoff: Annotated[Path, typer.Argument(help="The takeoff file.", metavar="TAKEOFF")],
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Also fail on open questions and on a missing or stale checked mark.",
        ),
    ] = False,
    mark: Annotated[
        str | None,
        typer.Option(
            "--mark",
            metavar="INITIALS",
            help="With no error, write the checked mark (who, today, each sheet's rev, the rows "
            "digest), changing no other byte of the file.",
        ),
    ] = None,
) -> None:
    """Check a takeoff (PLAN 5.1a): exit 1 on any error (or, with --strict, on open questions or
    a missing or stale checked mark). Writes nothing unless --mark is given."""
    import yaml

    from openfactory import takeoff as tk

    try:
        loaded = tk.load(takeoff)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        typer.echo(f"error: {takeoff}: {exc}", err=True)
        raise typer.Exit(1) from None
    if mark is not None:
        rep = tk.report(loaded)
        if rep.errors:
            typer.echo(tk.format_report(loaded, rep, strict=False), nl=False)
            typer.echo("not marked: fix the errors first", err=True)
            raise typer.Exit(1)
        try:
            text = tk.write_mark(loaded, mark)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from None
        takeoff.write_bytes(text.encode("utf-8"))  # the text as read: line endings kept
        loaded = tk.load(takeoff)
        typer.echo(f"marked {takeoff} as checked by {mark}")
    rep = tk.report(loaded)
    typer.echo(tk.format_report(loaded, rep, strict=strict), nl=False)
    if rep.errors or (strict and rep.strict_failures()):
        raise typer.Exit(1)


@takeoff_app.command("scaffold")
def takeoff_scaffold(
    takeoff: Annotated[Path, typer.Argument(help="The takeoff file.", metavar="TAKEOFF")],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="The scene file to write.", metavar="SCENE")
    ],
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing scene file.")
    ] = False,
    in_scenes: Annotated[
        bool,
        typer.Option("--in-scenes", help="Allow writing inside the project's scenes/ folder."),
    ] = False,
) -> None:
    """Write a scene skeleton from a takeoff (PLAN 5.1b) and report every row's outcome: exit 0
    when all are placed with no question, 3 when the scene was written with rows to resolve, 1
    when nothing was written."""
    import yaml

    from openfactory import takeoff as tk
    from openfactory.core.composite import project_root
    from openfactory.scaffold import scaffold

    target = output.resolve()
    root = project_root(target.parent if target.parent.exists() else Path.cwd())
    if root is not None and not in_scenes and target.is_relative_to(root / "scenes"):
        typer.echo(f"error: {output} is inside {root / 'scenes'}; pass --in-scenes to write there",
                   err=True)  # fmt: skip
        raise typer.Exit(1)
    if output.exists() and not force:
        typer.echo(f"error: {output} exists; pass --force to overwrite it", err=True)
        raise typer.Exit(1)
    try:
        result = scaffold(tk.load(takeoff))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        typer.echo(f"error: {takeoff}: {exc}", err=True)
        raise typer.Exit(1) from None
    output.write_bytes(result.text.encode("utf-8"))
    typer.echo(result.report().replace(result.scene_name, output.name, 1), nl=False)
    if result.overlaps:  # ruled 2026-11-19 (second set): written, and exit 1
        typer.echo(f"{output} was written; exit 1 for the overlap findings above (move what "
                   "layout placed, or give those rows their position from the drawing)",
                   )  # fmt: skip
    raise typer.Exit(result.exit_code())


@app.command("layout")
def layout_cmd(
    file: Annotated[
        Path, typer.Argument(help="A takeoff (.takeoff.yaml) or a scene.", metavar="FILE")
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="The scene file to write.", metavar="SCENE"),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing scene file.")
    ] = False,
    in_scenes: Annotated[
        bool,
        typer.Option("--in-scenes", help="Allow writing inside the project's scenes/ folder."),
    ] = False,
    in_place: Annotated[
        bool,
        typer.Option("--in-place", help="A scene: write the result back to the scene itself."),
    ] = False,
) -> None:
    """Place what no drawing placed (PLAN 5.5): for a takeoff, write its scene (-o) as `takeoff
    scaffold` does, every row with no position laid out and every pipe routed; for a scene,
    place again every component marked `laid_out: true` and nothing else, written to -o SCENE
    or, with --in-place, back to the scene; with neither it writes nothing. Deterministic, in
    Python, no browser. Every position it gives is marked laid out, not from a drawing."""
    import yaml

    from openfactory.core.composite import project_root

    data = None
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        typer.echo(f"error: {file}: {exc}", err=True)
        raise typer.Exit(1) from None
    if output is not None and in_place:
        typer.echo("error: give -o SCENE or --in-place, not both", err=True)
        raise typer.Exit(1)
    if isinstance(data, dict) and "takeoff" in data:
        if in_place:
            typer.echo(f"error: {file} is a takeoff; --in-place would write a scene over it. "
                       "Give -o SCENE, the scene to write", err=True)  # fmt: skip
            raise typer.Exit(1)
        if output is None:
            typer.echo("error: a takeoff needs -o SCENE, the scene to write", err=True)
            raise typer.Exit(1)
        takeoff_scaffold(file, output, force, in_scenes)
        return
    from openfactory.layout import relayout_scene

    if output is None and not in_place:  # ruled 2026-11-19: it never writes unasked
        typer.echo(f"error: nothing written; give -o SCENE to write the laid-out scene to a new "
                   f"file, or --in-place to write it back to {file.name}", err=True)  # fmt: skip
        raise typer.Exit(1)
    target = (output or file).resolve()
    root = project_root(target.parent if target.parent.exists() else Path.cwd())
    if root is not None and not in_scenes and target.is_relative_to(root / "scenes"):
        typer.echo(f"error: {target} is inside {root / 'scenes'}; pass --in-scenes to write there",
                   err=True)  # fmt: skip
        raise typer.Exit(1)
    if output is not None and output.exists() and not force:
        typer.echo(f"error: {output} exists; pass --force to overwrite it", err=True)
        raise typer.Exit(1)
    try:
        text, moved, notes = relayout_scene(file.read_text(encoding="utf-8"), file.parent,
                                            file.resolve())  # fmt: skip
    except (OSError, ValueError, KeyError) as exc:
        typer.echo(f"error: {file}: {exc}", err=True)
        raise typer.Exit(1) from None
    (output or file).write_bytes(text.encode("utf-8"))
    said = f": {', '.join(moved)}" if moved else "; nothing is marked laid_out"
    typer.echo(f"{file.name}: laid out {len(moved)} marked component(s){said}")
    for note in notes:
        typer.echo(f"  {note}")
    from openfactory.layout import placed_overlaps

    found = placed_overlaps(text, file.parent, moved, file.resolve()) if moved else []
    if found:  # ruled 2026-11-19 (second set): what it placed against everything
        typer.echo(f"overlaps, what layout placed against everything ({len(found)}):")
        for m in found:
            typer.echo(f"  {m}")
        typer.echo(f"{output or file} was written; exit 1 for the overlap findings above")
        raise typer.Exit(1)


def _pair(text: str) -> tuple[float, float]:
    try:
        x, y = (float(v) for v in text.split(","))
    except ValueError:
        raise typer.BadParameter(f"{text!r}: expected X,Y in pixels") from None
    return x, y


@app.command("review")
def review_cmd(
    takeoff: Annotated[Path, typer.Argument(help="The takeoff file.", metavar="TAKEOFF")],
    scene: Annotated[Path, typer.Argument(help="The scene made from it.", metavar="SCENE")],
    drawing: Annotated[
        Path, typer.Option("--drawing", help="The layout drawing, a PNG or SVG file.")
    ],
    datum_px: Annotated[
        str, typer.Option("--datum-px", help="Where the sheet's datum is in the picture: X,Y.")
    ],
    px_per_unit: Annotated[
        float, typer.Option("--px-per-unit", help="Pixels per sheet unit (the frame's units).")
    ],
    sheet: Annotated[
        str | None, typer.Option("--sheet", help="The layout sheet's id (if more than one).")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", help="The folder to write to (default: beside the takeoff)."
        ),  # fmt: skip
    ] = None,
    png: Annotated[
        bool, typer.Option("--png", help="Also write the overlay as a PNG (needs Playwright).")
    ] = False,
    in_repo: Annotated[
        bool, typer.Option("--in-repo", help="Allow writing inside the OpenFactory repository.")
    ] = False,
) -> None:
    """The scene's top view over its layout drawing, and a table of takeoff rows against scene
    components (PLAN 5.6). The drawing is loaded as an image only (PNG or SVG; anything else is
    refused by name; nothing is fetched). Written beside the takeoff or to --out: as private as
    the drawing."""
    from openfactory import review as rv
    from openfactory import takeoff as tk

    if rv.URL.match(str(drawing)):
        typer.echo(f"error: {drawing}: a URL; review reads a file you name and fetches nothing",
                   err=True)  # fmt: skip
        raise typer.Exit(1)
    folder = out if out is not None else rv.default_dir(takeoff)
    if rv.in_repository(folder) and not in_repo:
        typer.echo(
            f"error: {folder} is inside the OpenFactory repository; the review is as "
            "private as the drawing: name a folder outside it with --out",
            err=True,
        )
        raise typer.Exit(1)
    try:
        data_url = rv.drawing_data_url(drawing)
        result = rv.review(tk.load(takeoff), scene, sheet, _pair(datum_px), px_per_unit)
    except (OSError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    folder.mkdir(parents=True, exist_ok=True)
    stem = scene.name.removesuffix(".yaml")
    table, page = folder / f"{stem}.review.md", folder / f"{stem}.review.html"
    table.write_text(result.table(), encoding="utf-8")
    text = rv.overlay_html(result, data_url)
    page.write_text(text, encoding="utf-8")
    written = [table, page]
    if png:
        try:
            rv.render_png(text, folder / f"{stem}.review.png")
        except ImportError:
            typer.echo("error: --png needs Playwright (the dev extra); the HTML is written",
                       err=True)  # fmt: skip
            raise typer.Exit(1) from None
        written.append(folder / f"{stem}.review.png")
    counts = ", ".join(f"{n} {k}" for k, n in result.counts().items())
    typer.echo(f"{scene.name} on {result.sheet}: {counts}")
    for path in written:
        typer.echo(f"  wrote {path}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
