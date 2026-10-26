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
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { EventLink, fetchScene, streamState, type SceneDocument, type StateFrame } from "./api";
import { loadRapier, PlantPhysics } from "./physics";
import { buildComponent, floorFor, type ComponentView } from "./components";
import { cssColor, ISA101 } from "./isa101";
import { PartMeshes } from "./parts";
import { planSteps, strokeAt } from "./ticksync";

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
};

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("#app element missing from index.html");

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
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
  bounds.expandByPoint(new THREE.Vector3(bounds.min.x, floorY, bounds.min.z));
  frameCamera(bounds);
}

/** Next selector position on a click: a -> b -> a, or a -> center -> b -> a with 3 positions. */
function nextPosition(geometryPositions: unknown, current: string): string {
  const order = geometryPositions === 3 ? ["a", "center", "b"] : ["a", "b"];
  return order[(order.indexOf(current) + 1) % order.length];
}

function wireClicks(doc: SceneDocument, events: EventLink): void {
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  renderer.domElement.addEventListener("click", (e) => {
    pointer.set((e.clientX / window.innerWidth) * 2 - 1, -(e.clientY / window.innerHeight) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const pickable = views.flatMap((v) => v.pickable.map((o) => [o, v] as const));
    const hit = raycaster.intersectObjects(pickable.map(([o]) => o), false)[0];
    const view = hit && pickable.find(([o]) => o === hit.object)?.[1];
    if (!view) return;
    if (view.type === "pushbutton") events.send({ type: "press", id: view.id });
    if (view.type === "selector_switch") {
      const geometry = doc.components.find((c) => c.id === view.id);
      const current = String(lastFrame?.components[view.id]?.position ?? "a");
      events.send({ type: "select", id: view.id, value: nextPosition(geometry?.positions, current) });
    }
  });
}

let lastFrame: StateFrame | null = null;
let physics: PlantPhysics | null = null;
let events: EventLink | null = null;

let lastTick: number | null = null; // core tick the physics world has reached
const strokes = new Map<string, number>(); // pusher stroke at lastTick

/**
 * Advance the physics world to the frame's core tick (PLAN 2.0a): exactly (tick - lastTick) steps,
 * at most MAX_STEPS_PER_FRAME, with the inputs of the previous frame (as in the parity replay, a
 * tick moves parts with the inputs reached at the end of the tick before) and pusher strokes
 * interpolated across the steps. Each beam edge goes upstream with the core tick of its step.
 */
function stepToFrame(frame: StateFrame): void {
  if (!physics) return;
  const plan = planSteps(lastTick, frame.tick);
  const target = new Map<string, number>();
  for (const view of views) {
    if (view.type === "pusher") target.set(view.id, Number(frame.components[view.id]?.position_m ?? 0));
  }
  const status = window.__openfactory;
  for (let i = 1; i <= plan.steps; i++) {
    for (const [id, to] of target) physics.setPusher(id, strokeAt(strokes.get(id) ?? to, to, i, plan.steps));
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
  // core decides which parts exist; a new part starts at the core's position for this tick).
  for (const view of views) {
    const state = frame.components[view.id] ?? {};
    if (view.type === "belt_conveyor") physics.setBelt(view.id, Number(state.speed_mps ?? 0));
  }
  for (const [id, to] of target) {
    strokes.set(id, to);
    if (plan.steps === 0) physics.setPusher(id, to);
  }
  const alive = new Set(frame.parts.map((p) => p.id));
  for (const id of physics.partIds()) if (!alive.has(id)) physics.removePart(id);
  for (const p of frame.parts) physics.addPart(p.id, p.size, p.position, p.velocity);
  for (const id of physics.partIds()) {
    const pose = physics.part(id);
    if (pose) parts.setPose(id, pose.position, pose.rotation);
  }
}

function onFrame(doc: SceneDocument, frame: StateFrame): void {
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
  const running = views.filter((v) => v.type === "belt_conveyor" && frame.components[v.id]?.running);
  hud.textContent =
    `OpenFactory · ${doc.scene.name}\n` +
    `t = ${frame.t.toFixed(2)} s   tick ${frame.tick}   parts ${frame.parts.length}\n` +
    `running: ${running.map((v) => v.id).join(", ") || "none"}`;
}

async function start(): Promise<void> {
  const doc = await fetchScene();
  buildScene(doc);
  window.__openfactory.scene = doc.scene.name;
  window.__openfactory.components = doc.components.length;
  events = new EventLink();
  wireClicks(doc, events);
  try {
    const R = await loadRapier();
    physics = new PlantPhysics(R, { components: doc.components, friction: doc.scene.friction, dt: doc.dt });
    window.__openfactory.physics = `rapier ${R.version()}`;
  } catch (err) {
    window.__openfactory.physics = `off: ${String(err)}`; // parts follow the core positions instead
  }
  streamState((frame) => onFrame(doc, frame));
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
