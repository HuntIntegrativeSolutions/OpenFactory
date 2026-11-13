## What and why

<!-- One paragraph: what this changes and the PLAN task or issue it serves. -->

PLAN task:

## Evidence

<!-- Done-when evidence: test output, numbers before and after, a screenshot you looked at. -->

## Checklist

- [ ] `uv run pytest` passes; `npm test` in `web/` too if the viewer changed
      (`bash tools/ci_local.sh` runs everything CI runs)
- [ ] Changed scenes pass `openfactory validate`, `openfactory lint`, and `openfactory test` with
      their stand-in script
- [ ] Visual changes checked with `tools/screenshot.py`, and the image looked at
- [ ] The PLAN checkbox is ticked in the same commit that closes a task
- [ ] Every commit is signed off (`git commit -s`, DCO 1.1)
- [ ] No binary file over 1 MB is added
- [ ] This is my own work, copies nothing from a commercial product, and includes nothing
      derived from the internals of a commercial 3D simulator (CONTRIBUTING.md, "Original Work
      Policy")

OpenFactory is a training and demo tool, not a safety-rated simulation.
