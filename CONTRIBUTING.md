# Contributing to OpenFactory

Thank you for helping build an open simulator for the automation community.

## Original Work Policy

OpenFactory is an original work. It contains no code, models, textures, scene files, documentation
text, names, layouts or other assets from any commercial product. Interoperability with controllers
and vendor software is built from public documentation and vendor SDK documentation. Vendor SDKs,
vendor binaries and vendor-specific code are not part of this repository. The internals of Factory
I/O and other commercial 3D simulators have not been examined for this project and nothing derived
from them is accepted.

Every contributor confirms, for each contribution:

1. The work is **your own** (or already under a compatible license, recorded as described under
   "Licensing of Contributions").
2. It **copies nothing from a commercial product**: no code, 3D models, textures, scene files,
   documentation text, names, layouts or other assets.
3. It **includes nothing derived from the internals of a commercial 3D simulator** (including but
   not limited to Factory I/O): nothing obtained by decompiling, disassembling, extracting or
   studying its internal files.
4. Interoperability code is built from public documentation (protocol specifications, open
   standards such as IEC 61131, ISA-5.1 and ISA-101, published vendor manuals) and vendor SDK
   documentation. It does not add a vendor SDK, a vendor binary or vendor-specific code to this
   repository. Open-source libraries, textbooks and your own engineering experience are welcome.
5. If you are unsure whether something meets this policy, open an issue and ask before writing
   code.
6. Search only inside the repository (and your own scratch folder). Never run `find`, `ls -R`,
   `grep -r` or any other walk from above the repository root: on a shared drive it can cross
   folders you must not open (vendor software, virtual machines). If you need a file from
   outside the repository, ask the maintainer for it.

Contributions that violate this policy will be removed, and the contributor may be blocked.

## Developer Certificate of Origin

By submitting a pull request you certify the [DCO 1.1](https://developercertificate.org/). Sign off each
commit with `git commit -s`.

## Licensing of Contributions

- Code: Apache License 2.0
- 3D models, textures, images, documentation: CC-BY-4.0
- You must own the rights to anything you contribute, or it must already be under a compatible license
  (MIT, BSD, Apache-2.0, CC0, CC-BY). Record the source and license of any third-party asset in
  `web/public/models/ATTRIBUTION.md`.

## Development Setup

```bash
# Python core
uv sync --all-extras
uv run pytest

# Web viewer
cd web
npm install
npm test
npm run dev
```

### Required setup on drives without symlink support (WSL `/mnt/<drive>`, some network shares)

Some filesystems refuse symbolic links. This repository's home on WSL (`/mnt/e/…`, a Windows drive)
is one of them; the check recorded in `docs/research/symlink-result.txt` failed. Both toolchains need
a small adjustment there:

- **uv** cannot create `.venv` inside the repository, because a virtualenv symlinks its Python
  interpreter. Keep the environment on the Linux filesystem and copy packages instead of linking them.
  Export these before any `uv` command (for example in `~/.bashrc`):

  ```bash
  export UV_PROJECT_ENVIRONMENT=$HOME/.venvs/openfactory
  export UV_LINK_MODE=copy
  ```

- **npm** cannot create `node_modules/.bin` links. `web/.npmrc` sets `bin-links=false`, and the npm
  scripts in `web/package.json` call each tool through `node ./node_modules/<tool>/…`, so `npm run`
  and `npm test` work the same on every filesystem. Use the npm scripts rather than `npx`.

On a native Linux or macOS filesystem the variables are optional; everything else stays the same.

See `docs/PLAN.md` for the roadmap and `CLAUDE.md` for the conventions that AI-assisted contributions follow.
Human contributors should follow the same conventions.

## Before you push

The only pre-push check is the full CI script **on a fresh clone** (ruled 2026-11-10):

```bash
git clone /path/to/your/OpenFactory /tmp/of-check && cd /tmp/of-check
bash tools/ci_local.sh            # every step CI runs; it must end "CI OK"
```

A partial check is not a substitute: `ruff format --check .` also formats Python blocks in the
Markdown docs, and the docs-site tests read every page, so `uv run pytest tests/unit` on the files
you touched, or ruff on `src` and `tests` only, misses failures CI then reports. A fresh clone
catches files you forgot to add and anything a local build left behind. On a drive without
symlink support, clone to a Linux filesystem (the script needs no `UV_PROJECT_ENVIRONMENT` there).

## Style

- Python: `ruff` for lint and format, 4-space indent, type hints on public functions.
- TypeScript: `prettier` + `eslint` defaults from `web/`.
- Commits: imperative mood, conventional prefix (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Every new component ships with a unit test and a docstring describing its behavior.

## Safety Notice

OpenFactory is a training and demonstration tool. It is **not** a safety-rated simulator and must never be
used to validate safety instrumented functions, machine guarding, or any logic whose failure could cause
injury.
