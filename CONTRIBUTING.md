# Contributing to OpenFactory

Thank you for helping build an open simulator for the automation community.

## Clean-Room Policy

OpenFactory is an original, independently developed work. To keep it that way, every contributor agrees:

1. You have **not** decompiled, disassembled, extracted assets from, or studied the internal files of any
   commercial 3D PLC simulator (including but not limited to Factory I/O) for the purpose of contributing
   here.
2. You will **not** submit 3D models, textures, scene layouts, component names, UI designs, or
   documentation text derived from a commercial product.
3. You **may** use public knowledge: protocol specifications, open standards (IEC 61131, ISA-5.1,
   ISA-101), open-source libraries, textbooks, and your own engineering experience.
4. If you are unsure whether something is clean, open an issue and ask before writing code.

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
uv sync
uv run pytest

# Web viewer
cd web
npm install
npm run dev
```

See `docs/PLAN.md` for the roadmap and `CLAUDE.md` for the conventions that AI-assisted contributions follow.
Human contributors should follow the same conventions.

## Style

- Python: `ruff` for lint and format, 4-space indent, type hints on public functions.
- TypeScript: `prettier` + `eslint` defaults from `web/`.
- Commits: imperative mood, conventional prefix (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Every new component ships with a unit test and a docstring describing its behavior.

## Safety Notice

OpenFactory is a training and demonstration tool. It is **not** a safety-rated simulator and must never be
used to validate safety instrumented functions, machine guarding, or any logic whose failure could cause
injury.
