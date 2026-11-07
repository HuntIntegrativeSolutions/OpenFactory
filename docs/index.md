# OpenFactory

**An open-source 3D factory simulator for PLC training, prototyping and demos.**

Write a plant as a YAML scene file. Run it. Connect a real PLC or a soft PLC over Modbus TCP, or an
HMI over OPC UA, and watch the plant move in your browser. Every shipped scene also runs with a
Python stand-in PLC, so you can try it with nothing else installed.

!!! warning "Training and demo tool only"
    OpenFactory is not a safety-rated simulation. Never use it to validate safety functions.
    Read [what OpenFactory is not](what-openfactory-is-not.md) before relying on it.

## Start here

```bash
git clone https://github.com/HuntIntegrativeSolutions/OpenFactory
cd OpenFactory
uv sync
(cd web && npm ci && npm run build)
uv run openfactory run scenes/sorting-by-height.yaml --script examples/pyplc/sorting-by-height.py
# open http://127.0.0.1:8000/
```

Then connect a soft PLC with the [OpenPLC quickstart](guides/openplc-quickstart.md), or pick a
scene from the [scene guides](guides/README.md).

## What is here

- **[Scene guides](guides/README.md):** one per shipped scene, each with its OpenPLC program and
  its stand-in Python PLC.
- **[Scene format](scene-format.md)** and the **[component reference](components.md)**, with a
  page per component type: its parameters, its I/O ports, its faults, and how it behaves.
- **[Ignition and OPC UA](ignition.md):** serving the I/O table to an HMI and exporting tags.
- **[Versioning policy](versioning.md):** how releases and the scene format are versioned.
- **Decisions and research:** the architecture decision records and the notes behind them.
- **[Roadmap](PLAN.md):** the plan, milestone by milestone, with the reasoning for each task.

## Contributing

OpenFactory is an original, clean-room work under Apache-2.0 (code) and CC-BY-4.0 (models and
documentation). See
[CONTRIBUTING.md](https://github.com/HuntIntegrativeSolutions/OpenFactory/blob/main/CONTRIBUTING.md),
including the clean-room policy, and report security problems as
[SECURITY.md](https://github.com/HuntIntegrativeSolutions/OpenFactory/blob/main/SECURITY.md)
describes.
