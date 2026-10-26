# OpenFactory — Project Instructions

Open-source 3D factory and process simulator for PLC training, prototyping, and customer demos.
Scenes are declarative text files; a Python simulation core owns the I/O table and speaks industrial
protocols; a browser-based Three.js viewer renders the plant. Built by HIS Controls, released under
Apache 2.0 for anyone to use and customize.

**Read `docs/PLAN.md` before doing any implementation work.** It is the authoritative roadmap, with the
research and reasoning behind every architectural choice. `docs/research/` holds the supporting notes.

---

## Project Facts

| Item | Value |
|------|-------|
| Project name | OpenFactory |
| Owner | HIS Controls (Hunt Integrative Solutions, LLC) |
| License | Apache-2.0 (code), CC-BY-4.0 (3D models, textures, docs) |
| Repo root | `E:\02_Internal_Projects\OpenFactory` (WSL: `/mnt/e/02_Internal_Projects/OpenFactory`) |
| Python | 3.11+ (`src/openfactory`), managed with `uv`; tests with `pytest` |
| Web | TypeScript, Vite, Three.js, Rapier3D (`web/`) |
| Scene format | YAML, schema in `src/openfactory/core/schema.py` (JSON Schema exported to `web/public/scene.schema.json`) |
| Status | Milestone 0 (planning). See `docs/PLAN.md` for the current milestone |

---

## Clean-Room Rule (Non-Negotiable)

OpenFactory is an original work. It is inspired by the *category* of 3D PLC simulators, not by any
specific product's code or assets.

- **Never** read, decompile, extract, or reference the binaries, scene files, 3D models, textures,
  documentation PDFs, or installer contents of Factory I/O or any other commercial simulator.
- **Never** copy component names, scene names, UI layouts, or menu structures from a commercial product.
  Every name in OpenFactory is our own.
- Public knowledge is fine: protocol specifications, open-source libraries, textbooks, and general
  descriptions of what a conveyor or photo eye does.
- If a contributor or session has been exposed to commercial simulator internals, say so and stop. Tainted
  work cannot be merged.
- `E:\04_Software\` is off-limits for this project. Do not open it.

---

## Architecture (One Paragraph)

A **scene file** (YAML) declares components, their geometry, and their I/O tag bindings. The **Python core**
loads the scene, builds a component graph, and steps it at a fixed rate (default 50 Hz), updating an
**I/O table** of named tags. **Protocol servers** (Modbus TCP first, then EtherNet/IP via pycomm3 client
polling, OPC UA, MQTT Sparkplug) expose that table to a real or emulated PLC. The **web viewer** connects
over a WebSocket, receives component state at 20 Hz, and renders it with Three.js; Rapier handles
part-on-conveyor physics in the browser, with authoritative discrete state (sensor trip, cylinder position)
mirrored back to the core. **Exporters** turn a scene into an I/O list CSV, which feeds the existing HIS
Controls `/io-to-l5x` and `/io-to-ignition-tags` skills.

Full detail with rationale: `docs/PLAN.md` §3.

---

## Directory Layout

```
OpenFactory/
├── CLAUDE.md                 ← you are here
├── README.md                 ← public-facing overview
├── CONTRIBUTING.md           ← clean-room policy, DCO, style
├── LICENSE                   ← Apache-2.0
├── docs/
│   ├── PLAN.md               ← THE roadmap (milestones, tasks, acceptance criteria)
│   ├── research/             ← library evaluations, protocol notes, sources
│   └── adr/                  ← Architecture Decision Records (ADR-0001…)
├── src/openfactory/
│   ├── core/                 ← scene loader, schema, I/O table, scheduler
│   ├── components/           ← one module per component type (conveyor.py, photo_eye.py …)
│   ├── protocols/            ← modbus.py, logix.py, opcua.py, mqtt.py
│   ├── export/               ← io_list_csv.py, ignition_tags.py, l5x_stub.py
│   └── server/               ← FastAPI app + WebSocket state stream
├── web/                      ← Vite + TypeScript + Three.js viewer
├── scenes/                   ← shipped example scenes (*.yaml)
├── examples/                 ← PLC programs for the example scenes (OpenPLC .st, L5X)
├── tests/
│   ├── unit/                 ← pure-Python component tests (no network, no browser)
│   └── integration/          ← Modbus round-trip, Playwright viewer smoke tests
└── tools/                    ← dev scripts (scene validator, screenshot runner)
```

---

## Working Rules for Claude

### Before coding
1. Read the current milestone in `docs/PLAN.md` and pick the next unchecked task. Do not skip ahead.
2. Check `docs/adr/` for a decision that already covers your question before proposing a new approach.
3. For any new protocol work, query `ai-memory-vendor-docs` and `deep-research` MCP first. Do not guess
   register maps or CIP service codes.
4. On a drive without symlink support (WSL `/mnt/e`), set up uv and npm as in `CONTRIBUTING.md`,
   "Required setup on drives without symlink support" (`UV_PROJECT_ENVIRONMENT`, `UV_LINK_MODE=copy`,
   npm `bin-links=false`) before running any `uv` or `npm` command.

### Scene files
- Every scene is valid against the JSON Schema. Run `uv run openfactory validate scenes/<name>.yaml`
  (or `uv run openfactory validate scenes`) before committing; it exits 1 on any failure.
- Tag names follow HIS Controls convention: `AREA_EQUIP_NNN_SUFFIX`, all caps, underscores, ≤ 40 chars.
  Suffixes: `_DI`, `_DO`, `_AI`, `_AO`.
- Every component has an `id`, a `type`, a `position`, and a `description` (ISA-5.1 style, see
  `~/.claude/rules/code-quality.md`).
- Scenes must be runnable headless for CI:
  `openfactory run scenes/x.yaml --as-fast-as-possible --duration 10` (fast mode implies
  `--headless`; `--force TAG=VALUE` sets fixed outputs, and `--script examples/pyplc/x.py`
  runs a Python stand-in PLC, `step(io, t)` every tick, PLAN 2.10).

### Components
- One Python module per component type. Each exposes `inputs`, `outputs`, `step(dt)`, and `state()`.
- Physical behavior is deterministic given inputs and `dt`. No wall-clock reads inside `step`.
- Every component ships with a unit test and a one-paragraph behavior spec in its docstring.
- Add new component types to `docs/PLAN.md` §5 component table, not just the code.

### Web viewer
- Scene JSON is the only source of truth for geometry. No hard-coded positions in TypeScript.
- Verify every visual change with the Playwright screenshot runner (`tools/screenshot.py`) and look at
  the image before declaring it done.
- Follow ISA-101 colors from `~/.claude/rules/ignition-conventions.md` for status indication.

### Protocols
- Modbus register map is generated from the scene, documented in the exported I/O CSV, and locked in
  `scenes/<name>.<encoding>.modbus.yaml` (ADR-0004, one per analog encoding; `--analog-encoding`
  on `run`/`export` selects it): commit the lockfile with the scene and never change an
  address in it by hand (deleting a `# not in scene` line to free an address is fine). To fix an
  address, pin it in the binding (`{tag: X_DO, address: N}`).
- All protocol servers bind to `127.0.0.1` by default. `--bind 0.0.0.0` is an explicit opt-in.
- Never hardcode IP addresses in scenes. Connection settings live in `openfactory.toml` (machine-specific
  values in the git-ignored `openfactory.local.toml`). A scene's `plc` block selects `protocol` and
  `role`; `plc.bind` (loopback only) / `plc.port` are optional listen-address overrides, and PLC
  addresses are named `plc.connection` entries resolved from `openfactory.toml`.

### Testing and commits
- `uv run pytest` must pass before any commit. `npm test` in `web/` for viewer changes.
  `bash tools/ci_local.sh` runs exactly what CI runs (`.github/workflows/ci.yml`).
- Commit messages: imperative, conventional prefix (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Each PR or commit that closes a PLAN task ticks its checkbox in `docs/PLAN.md` in the same commit.

### What this project does NOT do
- No safety-rated simulation. OpenFactory is a training and demo tool. Say so in every README and scene.
- No vendor firmware emulation. We talk to PLCs; we do not pretend to be one.
- No copying of commercial assets, ever (see Clean-Room Rule).

---

## Related HIS Controls Tooling

| Need | Use |
|------|-----|
| Scene → L5X tags/AOIs | `/io-to-l5x` on the exported CSV |
| Scene → Ignition UDTs | `/io-to-ignition-tags` on the exported CSV |
| Perspective demo screen | `/perspective-view` |
| Sequence documentation for a demo | `/grafcet-chart-creator` |
| Emulator test of a demo PLC program | `/emulator-load`, `/emulator-test` |
| SOP lookup | `search_sops` on `industrial-sops` |

Global HIS Controls rules in `~/.claude/rules/` still apply (code quality, ISA naming, Ignition and
Rockwell conventions). Where they conflict with this file, this file wins for OpenFactory.
