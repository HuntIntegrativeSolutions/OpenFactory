# Viewer rendering and learning-layer ideas

Review of 2026-11-11, prompted by a public browser app that teaches microcontroller boards in 3D.
It led to one PLAN task (4.14, viewer lighting and materials) and two parked ideas.

## What was looked at, and what is used

- **Looked at:** the app's rendered pages in headless Chromium, its visible page text, its served
  HTML page, and the list of network requests the page made. Its JavaScript source was not read.
- **License:** none is stated. Neither the page nor its footer names one, and `/LICENSE` returns 404.
- **Used:** nothing. No code, text, names, layout or assets from it are in OpenFactory. The ideas
  below are written in our own terms against our own architecture. The rendering techniques are
  ordinary three.js practice, cited to three's documentation.

## Rendering

**Finding: geometry is not the gap.** OpenFactory's component models are already built in code.
`tools/make_models.py` builds each housing from boxes and cylinders as a kit of named parts, and
`web/src/housings.ts` places and stretches the parts per scene, merged per material. The difference
we saw is lighting and materials. Today the viewer has a hemisphere light and one directional light,
with no environment map, no tone mapping and no shadows. `metalness` and `roughness` on
`MeshStandardMaterial` therefore have nothing to reflect, and nothing grounds the plant on the floor.

**Spike, 2026-11-11, reverted and not committed.** Applied to `web/src/main.ts`:

- ACES filmic tone mapping.
- A PMREM environment built from three's `RoomEnvironment`.
- A shadow-casting directional light.
- Shadows on every component mesh and the floor.

Steel and parts gained form and the plant sat on the floor. The spike also had three faults
(owner's review, confirmed by sampling pixels from the `tools/screenshot.py` images):

1. **Tone mapping and the environment bleached the status colors.** The running belt went from
   `#24B457` (today) to `#8BDC93`, against `#22C55E` in the style table. The stopped belt went
   from `#8F95A0` to `#C6C9CD`, against `#9CA3AF`. Even today's lighting misses the hex values,
   because the belt is lit and shaded. An exact status color needs a material that the lights,
   the environment and tone mapping do not touch.
2. **Shadows were cast by everything.** The emitter and remover outline volumes darkened the belts,
   and the patches read as a status change.
3. **The shadows were hard,** and the shadow camera was a fixed 12 m box, not fitted to the scene.

*Ruled 2026-11-11:* status materials are unlit at every quality level, low included (PLAN 4.14).

Three.js r186 is the version in `web/package.json`. Its console reported that `PCFSoftShadowMap`
has been removed and that it falls back to `PCFShadowMap`. Pick the softening method from the r186
documentation in 4.14; this note does not assume one.

Three.js documentation for the techniques:

- Tone mapping and shadow map types: https://threejs.org/docs/#api/en/constants/Renderer and
  https://threejs.org/docs/#api/en/renderers/WebGLRenderer
- Excluding a material from tone mapping: https://threejs.org/docs/#api/en/materials/Material.toneMapped
- Environment lighting: https://threejs.org/docs/#api/en/scenes/Scene.environment,
  https://threejs.org/docs/#api/en/extras/PMREMGenerator,
  https://threejs.org/docs/#api/en/materials/MeshStandardMaterial.envMapIntensity
- `RoomEnvironment` (MIT, part of the three package):
  https://github.com/mrdoob/three.js/blob/r186/examples/jsm/environments/RoomEnvironment.js
- Shadows: https://threejs.org/docs/#api/en/lights/shadows/DirectionalLightShadow and
  https://threejs.org/docs/#api/en/core/Object3D.castShadow

## Learning-layer ideas (parked, ruled 2026-11-11: no task yet)

**A trainee's view of a component.** In run mode, click a component to see what it is in plain
words, its ports and bound tags, and the faults it can take.

- *What exists:* `<scene>.CONTEXT.md` (2.19) already holds every tag, component, parameter, port
  and fault kind, written for an assistant. The kit README (2.14) indexes a kit's files. The 4.1b
  form shows a component's values on click, in edit mode only.
- *What would be new:* only the presentation. A read-only panel in run mode that shows facts the
  core already has, beside the 3D, for a trainee rather than an assistant. It needs no new data,
  apart from a plain-language description per component type if the scene's ISA description is
  not enough.

**Exercise progress in the viewer.** A trainee sees how a scene's exercise is going while it runs.

- *What exists:* 2.11 already grades a program against the scene's `checks:` with a pass/fail
  report and a statistics block (`openfactory test`, `/api/stats`). Exam mode drives `exam: switch`
  inputs by hand, the instructor view (4.12) watches, and the stand-in script is a reference
  answer.
- *What would be new:* checks evaluated and shown live in the viewer during `run`, rather than
  only at the end of `test`. Possibly also an ordered series of exercises on one scene, and a
  control that runs the reference script on request. The grading itself exists.
