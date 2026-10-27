// OpenFactory viewer (PLAN 1.11, 1.12): loads the scene from the core, builds one mesh per
// component from its type's builder (web/src/components/), streams /ws/state into component status,
// parts, and a HUD, and publishes window.__openfactory for tools/screenshot.py (PLAN 1.13). Orbit
// with the mouse; click a pushbutton to press it or a selector to turn it. Status colors are ISA-101
// (green running/active, grey stopped/idle). Geometry comes only from the scene JSON.
//
// Physics (PLAN 1.12, 2.0a): Rapier loads lazily and runs web/src/physics.ts at the core's dt,
// stepped only when a state frame arrives and exactly as many times as the core ticked since the
// previous frame (web/src/ticksync.ts, at most 10 per frame), never on wall-clock time. Each frame
// then sets belt speeds and pusher strokes for the next steps and adds or removes parts (the core
// decides which exist); parts render at their Rapier poses, eased between frames; photo-eye beam
// edges go upstream on /ws/events as {type: "sensor"} events carrying the core tick of their step. The core records them with its tick and never applies them (PLAN §3.3):
// The headless model is authoritative for PLC tags through Milestone 2; browser sensor events are parity-only.
// A part first seen in a frame is added before the step for its spawn tick + 1 (spawnStep), the
// tick the core first moved it in, so browser and core parts start on the same tick.
//
// Reconnect: when the state socket reconnects, the server may have restarted with another scene,
// so the viewer refetches /api/scene, rebuilds the plant if the document changed, and always starts
// a fresh physics world, part set, and event link (frames are ignored until that is done).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { BELT_TYPES, EventLink, fetchScene, streamState, type PartState, type SceneDocument, type StateFrame, type Vec3 } from "./api";
import { loadRapier, PlantPhysics, type Rapier } from "./physics";
import { buildComponent, floorFor, type ComponentView } from "./components";
import { cssColor, ISA101 } from "./isa101";
import { PartMeshes } from "./parts";
import { planSteps, spawnStep, strokeAt } from "./ticksync";

window.__openfactory = {
  ready: false,
  scene: "",
  components: 0,
  frames: 0,
  simTime: 0,
  tick: 0,
  error: null,
  physics: "loading",
  sensorEvents: 0,
  physicsSteps: 0,
  droppedTicks: 0,
  firstTick: null,
  reloads: 0,
};

/** Phones report a device pixel ratio of 3 or more; rendering at that drops frames for no gain. */
const MAX_PIXEL_RATIO = 2;

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("#app element missing from index.html");

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, MAX_PIXEL_RATIO));
renderer.setSize(window.innerWidth, window.innerHeight);
app.appendChild(renderer.domElement);

const labels = new CSS2DRenderer();
labels.setSize(window.innerWidth, window.innerHeight);
labels.domElement.style.cssText = "position:absolute;top:0;left:0;pointer-events:none";
app.appendChild(labels.domElement);

const hud = document.createElement("div");
hud.style.cssText =
  `position:absolute;top:12px;left:16px;font:14px/1.45 Arial,sans-serif;color:${cssColor("normalText")};` +
  "pointer-events:none;white-space:pre";
hud.textContent = "OpenFactory: connecting…";
app.appendChild(hud);

const scene = new THREE.Scene();
scene.background = new THREE.Color(ISA101.background);
scene.add(new THREE.HemisphereLight(0xffffff, 0x334155, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
sun.position.set(3, 8, 5);
scene.add(sun);
const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.05, 200);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  labels.setSize(window.innerWidth, window.innerHeight);
});

function label(text: string): CSS2DObject {
  const el = document.createElement("div");
  el.textContent = text;
  el.style.cssText =
    `font:12px Arial,sans-serif;color:${cssColor("normalText")};background:rgba(31,42,54,0.75);` +
    "padding:1px 5px;border-radius:3px";
  return new CSS2DObject(el);
}

function frameCamera(bounds: THREE.Box3): void {
  const center = bounds.getCenter(new THREE.Vector3());
  const radius = bounds.getSize(new THREE.Vector3()).length() / 2;
  const direction = new THREE.Vector3(0.25, 0.75, 1.0).normalize();
  camera.position.copy(center).addScaledVector(direction, radius * 2.1);
  controls.target.copy(center);
  controls.update();
}

const parts = new PartMeshes();
scene.add(parts.group);
let views: ComponentView[] = [];
let plant: THREE.Object3D[] = []; // what buildScene added: component views, floor, grid

/** Remove the plant built by buildScene, labels included (CSS2D elements live in the DOM). */
function clearScene(): void {
  for (const object of plant) {
    object.traverse((o) => {
      if (o instanceof CSS2DObject) o.element.remove();
      if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) o.geometry.dispose();
    });
    scene.remove(object);
  }
  plant = [];
  views = [];
}

function buildScene(doc: SceneDocument): void {
  const floorY = floorFor(doc.components);
  const bounds = new THREE.Box3();
  views = doc.components.map((g) => buildComponent(g, { floorY }));
  for (const view of views) {
    scene.add(view.object);
    const tag = label(view.id);
    tag.position.copy(view.labelAnchor);
    view.object.add(tag);
    view.object.updateMatrixWorld(true);
    bounds.expandByObject(view.object);
  }
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(40, 40),
    new THREE.MeshStandardMaterial({ color: 0x18222d, roughness: 1 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = floorY - 0.001;
  const grid = new THREE.GridHelper(40, 80, 0x324152, 0x253240);
  grid.position.y = floorY;
  scene.add(floor, grid);
  plant = [...views.map((v) => v.object), floor, grid];
  bounds.expandByPoint(new THREE.Vector3(bounds.min.x, floorY, bounds.min.z));
  frameCamera(bounds);
}

/** Next selector position on a click: a -> b -> a, or a -> center -> b -> a with 3 positions. */
function nextPosition(geometryPositions: unknown, current: string): string {
  const order = geometryPositions === 3 ? ["a", "center", "b"] : ["a", "b"];
  return order[(order.indexOf(current) + 1) % order.length];
}

function wireClicks(): void {
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  renderer.domElement.addEventListener("click", (e) => {
    pointer.set((e.clientX / window.innerWidth) * 2 - 1, -(e.clientY / window.innerHeight) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const pickable = views.flatMap((v) => v.pickable.map((o) => [o, v] as const));
    const hit = raycaster.intersectObjects(pickable.map(([o]) => o), false)[0];
    const view = hit && pickable.find(([o]) => o === hit.object)?.[1];
    if (!view || !doc || !events) return;
    if (view.type === "pushbutton") events.send({ type: "press", id: view.id });
    if (view.type === "selector_switch") {
      const geometry = doc.components.find((c) => c.id === view.id);
      const current = String(lastFrame?.components[view.id]?.position ?? "a");
      events.send({ type: "select", id: view.id, value: nextPosition(geometry?.positions, current) });
    }
  });
}

let doc: SceneDocument | null = null; // the scene the plant was built from
let loading = true; // (re)loading the scene: frames are ignored
let rapier: Rapier | null = null;
let lastFrame: StateFrame | null = null;
let physics: PlantPhysics | null = null;
let events: EventLink | null = null;

let lastTick: number | null = null; // core tick the physics world has reached
const strokes = new Map<string, number>(); // pusher stroke, stopper raise, deck angle at lastTick
const corePositions = new Map<string, Vec3>(); // each part's core position in the last frame

/**
 * Advance the physics world to the frame's core tick (PLAN 2.0a): exactly (tick - lastTick) steps,
 * at most MAX_STEPS_PER_FRAME, with the inputs of the previous frame (as in the parity replay, a
 * tick moves parts with the inputs reached at the end of the tick before) and pusher strokes
 * interpolated across the steps. Each beam edge goes upstream with the core tick of its step.
 */
function stepToFrame(frame: StateFrame): void {
  if (!physics) return;
  const plan = planSteps(lastTick, frame.tick);
  const target = new Map<string, number>(); // pusher strokes (m)
  const raise = new Map<string, number>(); // stopper blades (fraction raised)
  const turn = new Map<string, number>(); // turntable decks (degrees)
  for (const view of views) {
    const state = frame.components[view.id];
    if (view.type === "pusher") target.set(view.id, Number(state?.position_m ?? 0));
    if (view.type === "stopper") raise.set(view.id, Number(state?.position ?? 0));
    if (view.type === "turntable") turn.set(view.id, Number(state?.angle_deg ?? 0));
  }
  // Parts the core places this frame (held, or on a turning deck) move from their last core
  // position to this frame's across the steps instead of being simulated (PLAN §3.3).
  const following = frame.parts.filter((p) => p.follow && physics!.hasPart(p.id));
  // Parts the core spawned during this frame's ticks join on the tick it first moved them.
  const known = new Set(physics.partIds());
  const joining = new Map<number, PartState[]>();
  for (const p of frame.parts) {
    const i = known.has(p.id) || !p.spawn_position ? 0 : spawnStep(plan, p.spawn_tick);
    if (i) joining.set(i, [...(joining.get(i) ?? []), p]);
  }
  const status = window.__openfactory;
  for (let i = 1; i <= plan.steps; i++) {
    for (const p of joining.get(i) ?? []) physics.addPart(p.id, p.size, p.spawn_position ?? p.position);
    for (const [id, to] of target) physics.setPusher(id, strokeAt(strokes.get(id) ?? to, to, i, plan.steps));
    for (const [id, to] of raise) physics.setStopper(id, strokeAt(strokes.get(id) ?? to, to, i, plan.steps));
    for (const [id, to] of turn) physics.setTurntable(id, strokeAt(strokes.get(id) ?? to, to, i, plan.steps));
    for (const p of following) {
      const from = corePositions.get(p.id) ?? p.position;
      physics.follow(p.id, [0, 1, 2].map((a) => strokeAt(from[a], p.position[a], i, plan.steps)) as Vec3);
    }
    const tick = plan.firstTick + i - 1;
    for (const edge of physics.step()) {
      events?.send({ t: frame.t, tick, type: "sensor", id: edge.id, value: edge.blocked });
      status.sensorEvents = Number(status.sensorEvents) + 1;
    }
  }
  status.physicsSteps = Number(status.physicsSteps) + plan.steps;
  status.droppedTicks = Number(status.droppedTicks) + plan.dropped;
  lastTick = frame.tick;
  // Inputs for the steps of the next frame: belt speeds, plate strokes, and the part set (the
  // core decides which parts exist; a part not added above starts at the core's pose for this tick).
  for (const view of views) {
    const state = frame.components[view.id] ?? {};
    if (BELT_TYPES.has(view.type) || view.type === "turntable") physics.setBelt(view.id, Number(state.speed_mps ?? 0));
  }
  for (const [id, to] of target) {
    strokes.set(id, to);
    if (plan.steps === 0) physics.setPusher(id, to);
  }
  for (const [id, to] of raise) {
    strokes.set(id, to);
    if (plan.steps === 0) physics.setStopper(id, to);
  }
  for (const [id, to] of turn) {
    strokes.set(id, to);
    if (plan.steps === 0) physics.setTurntable(id, to);
  }
  for (const p of frame.parts) {
    if (p.follow && plan.steps === 0) physics.follow(p.id, p.position);
    if (!p.follow) physics.release(p.id, p.velocity); // no-op unless it was followed
    corePositions.set(p.id, p.position);
  }
  for (const id of corePositions.keys()) if (!frame.parts.some((p) => p.id === id)) corePositions.delete(id);
  const alive = new Set(frame.parts.map((p) => p.id));
  for (const id of physics.partIds()) if (!alive.has(id)) physics.removePart(id);
  for (const p of frame.parts) physics.addPart(p.id, p.size, p.position, p.velocity);
  for (const id of physics.partIds()) {
    const pose = physics.part(id);
    if (pose) parts.setPose(id, pose.position, pose.rotation);
  }
}

function onFrame(frame: StateFrame): void {
  if (loading || !doc) return;
  lastFrame = frame;
  for (const view of views) view.update(frame.components[view.id] ?? {});
  parts.update(frame.parts);
  stepToFrame(frame);
  const status = window.__openfactory;
  status.frames += 1;
  status.simTime = frame.t;
  status.tick = frame.tick;
  status.firstTick ??= frame.tick;
  status.parts = parts.count;
  status.ready = true;
  const running = views.filter((v) => (BELT_TYPES.has(v.type) || v.type === "turntable") && frame.components[v.id]?.running);
  hud.textContent =
    `OpenFactory · ${doc.scene.name}\n` +
    `t = ${frame.t.toFixed(2)} s   tick ${frame.tick}   parts ${frame.parts.length}\n` +
    `running: ${running.map((v) => v.id).join(", ") || "none"}`;
}

/**
 * Fetch /api/scene and (re)build: the plant only if the document changed, and always a fresh
 * physics world, part set, tick sync, and event link, because a reconnect may follow a server
 * restart (ticks and part ids start again).
 */
async function loadScene(): Promise<void> {
  loading = true;
  const next = await fetchScene();
  const status = window.__openfactory;
  if (!doc || JSON.stringify(next) !== JSON.stringify(doc)) {
    if (doc) status.reloads = Number(status.reloads) + 1;
    clearScene();
    doc = next;
    buildScene(doc);
    status.scene = doc.scene.name;
    status.components = doc.components.length;
  }
  physics?.free();
  physics = null;
  parts.clear();
  lastTick = null;
  strokes.clear();
  corePositions.clear();
  lastFrame = null;
  events = new EventLink();
  if (rapier) {
    physics = new PlantPhysics(rapier, { components: doc.components, friction: doc.scene.friction, dt: doc.dt });
  }
  loading = false;
}

async function start(): Promise<void> {
  await loadScene();
  wireClicks();
  try {
    rapier = await loadRapier();
    if (doc) physics = new PlantPhysics(rapier, { components: doc.components, friction: doc.scene.friction, dt: doc.dt });
    window.__openfactory.physics = `rapier ${rapier.version()}`;
  } catch (err) {
    window.__openfactory.physics = `off: ${String(err)}`; // parts follow the core positions instead
  }
  streamState(onFrame, () => {
    loadScene().catch((err: unknown) => {
      window.__openfactory.error = String(err);
      hud.textContent = `OpenFactory: ${String(err)}`;
    });
  });
}

start().catch((err: unknown) => {
  window.__openfactory.error = String(err);
  hud.textContent = `OpenFactory: ${String(err)}`;
});

renderer.setAnimationLoop(() => {
  controls.update();
  parts.animate(); // eases meshes toward their latest pose between 20 Hz frames; never steps physics
  renderer.render(scene, camera);
  labels.render(scene, camera);
});
