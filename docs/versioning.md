# Versioning policy

OpenFactory follows [Semantic Versioning 2.0.0](https://semver.org/) from release 0.2.0 (ruled
2026-11-06, PLAN 4.8). Two version numbers matter, and they are independent:

- the **package version** (`src/openfactory/__init__.py`, which `pyproject.toml` reads, and
  `web/package.json`), such as `0.2.0`;
- the **scene format version**, the `openfactory: "0.1"` line every scene, library composite and
  bundle manifest declares, and the `$id` of `web/public/scene.schema.json`.

OpenFactory is a training and demo tool, not a safety-rated simulation. No version of it is
suitable for validating safety functions.

## Package version: what bumps which number

The public interface is what a user's files, programs and scripts depend on:

- the CLI: commands, options, exit codes;
- the scene format (below), library composites, and bundles (`.ofz`);
- the Modbus register map rules and the address lockfiles (ADR-0004), the OPC UA address space, and
  the Ignition and L5X exports;
- the HTTP and WebSocket API (`/api/...`, the state and event streams) and recordings (`.ofrec`);
- the stand-in PLC script interface (`step(io, t)`, `openfactory.pyplc`).

| Change | 1.0.0 and later | Before 1.0.0 |
|---|---|---|
| A breaking change to the public interface: something removed or renamed, a meaning or default changed so that an existing scene, program, script or client behaves differently | **major** | **minor**, and the changelog says it breaks |
| New capability that existing files and clients do not notice: a component type, an optional scene key, a CLI option, a protocol, an export | **minor** | **minor** |
| A fix that makes behaviour match the documentation, documentation, performance with no behaviour change | **patch** | **patch** |

A change to simulated behaviour that a PLC program can observe (a sensor edge a tick earlier, a
part that used to fall and now does not) counts as a fix when the documentation already promised
the new behaviour, and as breaking otherwise. The changelog names it either way.

## Scene format version

The scene format version changes only when the format breaks: a key removed or renamed, a value
whose meaning or default changes, or a structure an older reader would misread. Adding optional
keys or component types does not change it. That is why the format stayed `0.1` through
Milestone 2, which added component types and optional keys only, while the package went to 0.2.0
(ruled 2026-11-06). A scene that uses a key or a component type added in a later release needs
that release; an older one rejects the scene at load time with the unknown key or type named.

`openfactory migrate SCENE...` (PLAN 4.7) rewrites scenes, library composites and example scenes
written in an older format version to the current one, in place. It edits only the text the
format change touches, so comments, key order, quoting and layout stay as they were; a file that
is already current is left byte for byte. `openfactory migrate --check scenes` writes nothing and
exits 1 if any file needs migrating; CI runs it on every shipped scene. A bundle (`.ofz`) is
unpacked, its scene migrated, and bundled again.

The format is still `0.1` and has never broken, so there is no migration yet and `migrate` changes
nothing. The first breaking change bumps the format version, adds its migration to
`src/openfactory/core/migrate.py`, and copies every shipped scene, as it was, into
`tests/migrate/<old version>/`: that corpus of every shipped scene at every past format version
must migrate to the shipped files exactly (`tests/unit/test_migrate.py`).

The exported JSON Schema shows both versions: its `$id` is the format
(`urn:openfactory:scene:0.1`), and its `$comment` names the package release that exported it.

## Other files that carry a version

- **Address lockfiles** (`*.modbus.yaml`): never edited by hand; their rules are ADR-0004's.
- **Recordings** (`.ofrec`): the header names the package version that recorded them.
- **Bundles** (`.ofz`): the manifest names the bundle version (1), the scene format version, and
  the package that built it.

## Release steps

A release is cut on `main` with a clean tree and green CI. The owner tags it.

1. Set the new version in `src/openfactory/__init__.py`, `web/package.json` and the two
   `"version"` fields at the top of `web/package-lock.json`.
2. Regenerate the JSON Schema: `uv run python -m openfactory.core.schema`. Its `$comment` names
   the package version, so CI's schema check fails until it is regenerated.
3. **Rebuild every demo kit:** `uv run python tools/build_kit.py`. Each kit's recording carries the
   package version in its header, so every version bump makes all six kits stale, and
   `tools/build_kit.py --check` fails in CI until they are rebuilt.
4. Regenerate the changelog after the last commit that goes in: `uv run python
   tools/changelog.py`.
5. Run `bash tools/ci_local.sh`, commit, push, and wait for CI to pass.
6. Tag `vX.Y.Z` on that commit (the owner does this).
