# ADR-0001: Declarative scenes, Python core, browser viewer

**Status:** Accepted, 2026-10-24
**Deciders:** HIS Controls (T. Hunt), Claude Fable 5.1

## Context

OpenFactory must be (a) authorable by an AI assistant and by scripts, (b) runnable with zero install by
students and customers, (c) connectable to real industrial controllers, and (d) verifiable by the AI that
writes it. Commercial simulators store scenes in opaque editor formats and run as native desktop apps, which
satisfies none of these.

## Decision

1. **Scenes are YAML files** validated by a pydantic schema. The schema is the contract for the core, the
   viewer, the exporters, and any future editor.
2. **A Python process is the simulation authority.** It owns the I/O table, steps components at a fixed
   rate, and hosts all protocol servers and clients (pymodbus, pycomm3, asyncua, paho).
3. **The viewer is a browser application** built with Three.js and Rapier3D, served by the Python process,
   talking over WebSockets. The browser runs rigid-body physics for visuals and reports discrete events; the
   core decides what the PLC sees.
4. **A headless kinematic model in Python** mirrors the browser physics closely enough for CI and PLC-logic
   tests without a browser.
5. **Licenses:** Apache-2.0 for code, CC-BY-4.0 for assets and docs. All runtime dependencies are
   OSI-approved permissive licenses (MIT, MIT-0, BSD, ISC, PSF-2.0, Apache-2.0, MPL-2.0) or LGPL used
   dynamically. `tools/third_party.py` enforces this list when it regenerates `THIRD_PARTY.md`.
   *(Amended 2026-10-25: the original list omitted MIT-0, ISC, and PSF-2.0, which arrive
   transitively through cffi, typer, and pydantic.)*

## Alternatives considered

- **Godot 4 desktop app.** Better physics tooling and a real editor, but requires install, cannot be
  driven by Playwright, and splits protocol work into GDScript/C# where industrial libraries are thin.
- **Unity.** Proprietary license, unsuitable for an open-source community project.
- **Physics entirely in Python (PyBullet).** Clean single-authority model, but streaming every part's pose
  at 60 Hz over WebSocket is wasteful and visuals stutter on jitter. Rejected; kept as a possible server-side
  mode for multi-viewer classrooms later.
- **Fork Gazebo or Webots.** Robotics focus, heavy, wrong scene abstractions.

## Consequences

- Positive: scenes diff in git, AI can generate and test them, viewer is a URL, protocol work stays in the
  richest ecosystem, HIS Controls' existing L5X and Ignition generators consume the scene export directly.
- Negative: two motion models (Rapier in browser, kinematics in Python) must be kept in agreement; a parity
  test is mandatory from Milestone 1. WebSocket adds a few tens of milliseconds of visual latency, which is
  acceptable for training but not for closed-loop timing studies.
- The first supported controller is OpenPLC over Modbus TCP, because it is free end to end; Rockwell and
  Ignition paths follow in Milestone 2.
