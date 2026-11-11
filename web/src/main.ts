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
// Faults (PLAN 2.6): right-click (or long-press) a component for the fault menu (faultmenu.ts);
// a faulted component's label shows the fault in ISA-101 alarm yellow.
//
// Reconnect: when the state socket reconnects, the server may have restarted with another scene,
// so the viewer refetches /api/scene, rebuilds the plant if the document changed, and always starts
// a fresh physics world, part set, and event link (frames are ignored until that is done).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { BELT_TYPES, EventLink, fetchScene, streamInstructor, streamState, type PartState, type SceneDocument, type StateFrame, type Vec3 } from "./api";
import { loadRapier, offDeck, PlantPhysics, tiltDeg, type Rapier } from "./physics";
import { buildComponent, floorFor, type ComponentView } from "./components";
import { attachHousings } from "./housings";
import { attachMeshes } from "./meshes";
import { cssColor, ISA101 } from "./isa101";
import { ExamPanel } from "./exampanel";
import { TrendPanel } from "./trend";
import { FaultMenu } from "./faultmenu";
import { faultOf, isInstructorPath, tokenFromHash } from "./instructor";
import { currentToken, rememberToken } from "./guarded";
import { PartMeshes } from "./parts";
import { planSteps, spawnStep, strokeAt } from "./ticksync";
import { FrameStats } from "./framestats";
import { framedBounds, framePose } from "./framing";

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
  maxTiltDeg: 0,
  maxOffDeckM: 0,
  offDeck: null,
  frameMs: null,
  frameP95Ms: null,
  renderMs: 0,
  drawCalls: 0,
  stateMs: 0,
  tilt: null,
  firstTick: null,
  reloads: 0,
  exam: false,
  viewers: 0,
  instructor: false, // PLAN 4.12: "connecting", "on" or "refused: <reason>" on /instructor
  faultMarkers: [] as string[], // ids whose label shows a fault marker

  meshes: { loaded: 0, failed: 0 },
  housings: { loaded: 0, failed: 0, types: [] as string[] },
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
hud.id = "hud";
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
    "padding:1px 5px;border-radius:3px;white-space:nowrap";
  return new CSS2DObject(el);
}

/** Point the camera at the components' bounds (web/src/framing.ts). */
function frameCamera(bounds: THREE.Box3): void {
  const pose = framePose(bounds, camera.fov);
  camera.position.copy(pose.position);
  controls.target.copy(pose.target);
  controls.update();
}

const parts = new PartMeshes();
scene.add(parts.group);
let views: ComponentView[] = [];
let plant: THREE.Object3D[] = []; // what buildScene added: component views, floor, grid
const labelsById = new Map<string, { label: CSS2DObject; fault: string | null }>();

/** The instructor view (PLAN 4.12): /instructor, fault markers even in exam mode, read-only
 * exam switches. A token in the URL fragment is kept for this tab and taken off the address bar. */
const INSTRUCTOR = isInstructorPath(location.pathname);
if (INSTRUCTOR) {
  const fromHash = tokenFromHash(location.hash);
  if (fromHash) {
    rememberToken(fromHash);
    history.replaceState(null, "", location.pathname + location.search);
  }
  window.__openfactory.instructor = "connecting";
}

/** Show a component's injected fault on its label (ISA-101 alarm yellow), or clear it. */
function markFault(id: string, fault: string | null): void {
  const entry = labelsById.get(id);
  if (!entry || entry.fault === fault) return;
  entry.fault = fault;
  window.__openfactory.faultMarkers = [...labelsById].filter(([, e]) => e.fault).map(([k]) => k);
  const el = entry.label.element;
  el.textContent = fault ? `${id} ⚠ ${fault.replace("_", " ")}` : id;
  el.style.background = fault ? cssColor("alarmMedium") : "rgba(31,42,54,0.75)";
  el.style.color = fault ? cssColor("background") : cssColor("normalText");
}

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
  labelsById.clear();
}

function buildScene(doc: SceneDocument): void {
  const floorY = floorFor(doc.components);
  views = doc.components.map((g) => buildComponent(g, { floorY }));
  for (const view of views) {
    scene.add(view.object);
    const tag = label(view.id);
    tag.position.copy(view.labelAnchor);
    view.object.add(tag);
    labelsById.set(view.id, { label: tag, fault: null });
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
  frameCamera(framedBounds(views.map((v) => v.object)));
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
const coreYaws = new Map<string, number>(); // each part's drawn yaw in the last frame (PLAN 2.5b)

/** A part's yaw to draw, degrees (PLAN 2.5b): its own, plus the deck's angle while a turntable
 * carries it (the core sends it relative to the deck). */
function drawnYaw(p: PartState, frame: StateFrame): number {
  const own = typeof p.yaw_deg === "number" ? p.yaw_deg : 0;
  const deck = p.carrier ? views.find((v) => v.id === p.carrier) : undefined;
  return deck?.type === "turntable" ? own + Number(frame.components[deck.id]?.angle_deg ?? 0) : own;
}

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
  const swing = new Map<string, number>(); // diverter arms (degrees)
  const rise = new Map<string, number>(); // lift decks (m above the bottom)
  for (const view of views) {
    const state = frame.components[view.id];
    if (view.type === "pusher") target.set(view.id, Number(state?.position_m ?? 0));
    if (view.type === "stopper") raise.set(view.id, Number(state?.position ?? 0));
    if (view.type === "turntable") turn.set(view.id, Number(state?.angle_deg ?? 0));
    if (view.type === "diverter_arm") swing.set(view.id, Number(state?.angle_deg ?? 0));
    if (view.type === "lift") rise.set(view.id, Number(state?.height_m ?? 0));
  }
  // Parts the core places this frame (held, or on a turning deck) move from their last core
  // position to this frame's across the steps instead of being simulated (PLAN §3.3).
  const following = frame.parts.filter((p) => p.follow && physics!.hasPart(p.id));
  // A part the core stopped placing during these ticks is simulated again from its last placed
  // position for all of them, not held still until the next frame.
  for (const p of frame.parts) if (!p.follow) physics.release(p.id, p.velocity); // no-op unless it was followed
  // Parts the core spawned during this frame's ticks join on the tick it first moved them.
  const known = new Set(physics.partIds());
  const joining = new Map<number, PartState[]>();
  for (const p of frame.parts) {
    const i = known.has(p.id) || !p.spawn_position ? 0 : spawnStep(plan, p.spawn_tick);
    if (i) joining.set(i, [...(joining.get(i) ?? []), p]);
  }
  const status = window.__openfactory;
  for (let i = 1; i <= plan.steps; i++) {
    for (const p of joining.get(i) ?? []) physics.addPart(p.id, p.size, p.spawn_position ?? p.position, [0, 0, 0], drawnYaw(p, frame));
    for (const [id, to] of target) physics.setPusher(id, strokeAt(strokes.get(id) ?? to, to, i, plan.steps));
    for (const [id, to] of raise) physics.setStopper(id, strokeAt(strokes.get(id) ?? to, to, i, plan.steps));
    for (const [id, to] of turn) physics.setTurntable(id, strokeAt(strokes.get(id) ?? to, to, i, plan.steps));
    for (const [id, to] of swing) physics.setDiverter(id, strokeAt(strokes.get(id) ?? to, to, i, plan.steps));
    for (const [id, to] of rise) physics.setLift(id, strokeAt(strokes.get(id) ?? to, to, i, plan.steps));
    for (const p of following) {
      const from = corePositions.get(p.id) ?? p.position;
      const yaw = drawnYaw(p, frame);
      physics.follow(
        p.id,
        [0, 1, 2].map((a) => strokeAt(from[a], p.position[a], i, plan.steps)) as Vec3,
        strokeAt(coreYaws.get(p.id) ?? yaw, yaw, i, plan.steps),
      );
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
    if (BELT_TYPES.has(view.type) || view.type === "turntable" || view.type === "lift") physics.setBelt(view.id, Number(state.speed_mps ?? 0));
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
  for (const [id, to] of swing) {
    strokes.set(id, to);
    if (plan.steps === 0) physics.setDiverter(id, to);
  }
  for (const [id, to] of rise) {
    strokes.set(id, to);
    if (plan.steps === 0) physics.setLift(id, to);
  }
  // Joining a run (or a restarted one): devices start at this frame's pose, not swept to it
  // through the parts added below during the next step (PLAN 2.9, the tipped box).
  if (plan.reset) physics.snapDevices();
  for (const p of frame.parts) {
    if (p.follow && plan.steps === 0) physics.follow(p.id, p.position, drawnYaw(p, frame));
    corePositions.set(p.id, p.position);
    coreYaws.set(p.id, drawnYaw(p, frame));
  }
  for (const id of corePositions.keys()) if (!frame.parts.some((p) => p.id === id)) corePositions.delete(id);
  for (const id of coreYaws.keys()) if (!frame.parts.some((p) => p.id === id)) coreYaws.delete(id);
  const alive = new Set(frame.parts.map((p) => p.id));
  for (const id of physics.partIds()) if (!alive.has(id)) physics.removePart(id);
  for (const p of frame.parts) physics.addPart(p.id, p.size, p.position, p.velocity, drawnYaw(p, frame));
  const carried = new Set(frame.parts.filter((p) => p.carrier).map((p) => p.id));
  const carrierOf = new Map(frame.parts.filter((p) => p.carrier && !p.follow).map((p) => [p.id, p.carrier!]));
  for (const id of physics.partIds()) {
    const pose = physics.part(id);
    if (!pose) continue;
    parts.setPose(id, pose.position, pose.rotation);
    // The core holds every part it carries upright: a drawn tilt there is the viewer departing
    // from it (PLAN 2.9). A falling part (off a belt end or pushed into a chute) may tumble.
    const tilt = carried.has(id) ? tiltDeg(pose.rotation) : 0;
    if (tilt > Number(status.maxTiltDeg)) {
      status.maxTiltDeg = tilt;
      status.tilt = { part: id, tick: frame.tick, deg: tilt, position: pose.position };
    }
    // ... and on its carrier's deck (the position check): a part drawn off it, such as a pushed box
    // thrown beyond the divert belt, is the viewer departing from the core too. Parts the core
    // places (follow) are drawn at its position anyway.
    const carrier = carrierOf.get(id);
    const deck = carrier === undefined ? undefined : doc?.components.find((g) => g.id === carrier);
    if (deck) {
      const off = offDeck(deck, pose.position, Number(frame.components[deck.id]?.angle_deg ?? 0));
      if (off > Number(status.maxOffDeckM)) {
        status.maxOffDeckM = off;
        status.offDeck = { part: id, carrier, tick: frame.tick, m: off, position: pose.position };
      }
    }
  }
}

function onFrame(frame: StateFrame): void {
  if (loading || !doc) return;
  const start = performance.now();
  lastFrame = frame;
  for (const view of views) {
    const state = frame.components[view.id] ?? {};
    view.update(state);
    markFault(view.id, faultOf(frame, view.id));
  }
  parts.update(frame.parts, (p) => drawnYaw(p, frame));
  stepToFrame(frame);
  const status = window.__openfactory;
  status.frames += 1;
  status.simTime = frame.t;
  status.tick = frame.tick;
  status.firstTick ??= frame.tick;
  status.parts = parts.count;
  status.viewers = frame.viewers ?? 1;
  if (frame.instructor) status.instructor = "on";
  examPanel?.sync(frame.io); // switches are the core's: another viewer's turn shows here (PLAN 4.3)
  status.ready = true;
  const running = views.filter((v) => (BELT_TYPES.has(v.type) || v.type === "turntable" || v.type === "lift") && frame.components[v.id]?.running);
  hud.textContent =
    `OpenFactory · ${doc.scene.name}${doc.mode === "replay" ? "   REPLAY" : ""}` +
    `${INSTRUCTOR ? "   INSTRUCTOR" : ""}\n` +
    `t = ${frame.t.toFixed(2)} s   tick ${frame.tick}   parts ${frame.parts.length}` +
    `${frame.viewers === undefined ? "" : `   viewers ${frame.viewers}`}\n` +
    `running: ${running.map((v) => v.id).join(", ") || "none"}\n` +
    frameLine();
  examPanel?.avoid(hud); // below the status lines on a narrow screen
  stateCost.frame(start, performance.now() - start);
  status.stateMs = stateCost.renderMs; // main-thread ms per state frame: scene updates and physics
}

const stateCost = new FrameStats(40);
let examPanel: ExamPanel | null = null; // set in start(); synced from every state frame

const frameStats = new FrameStats();

/**
 * The stats overlay's frame line (PLAN 2.9): mean and p95 time between rendered frames, then
 * where the main thread's time goes: rendering per frame, and per 20 Hz state frame the scene
 * updates and viewer physics. A frame time well above those is spent on the GPU (or its driver).
 */
function frameLine(): string {
  const mean = frameStats.meanMs;
  const p95 = frameStats.p95Ms;
  if (mean === null || p95 === null) return "frame: measuring";
  return `frame ${mean.toFixed(1)} ms (${(1000 / mean).toFixed(0)} fps), p95 ${p95.toFixed(1)} ms   ` +
    `render ${frameStats.renderMs.toFixed(1)} ms   state ${stateCost.renderMs.toFixed(1)} ms   ` +
    `draw calls ${renderer.info.render.calls}`;
}

/**
 * Fetch /api/scene and (re)build: the plant only if the document changed, and always a fresh
 * physics world, part set, tick sync, and event link, because a reconnect may follow a server
 * restart (ticks and part ids start again).
 */
/** A recording served by `openfactory replay` (PLAN 2.12): parts are drawn where it has them,
 * with no viewer physics, as when Rapier fails to load. */
function replaying(): boolean {
  return doc?.mode === "replay";
}

async function loadScene(): Promise<void> {
  loading = true;
  const next = await fetchScene();
  const status = window.__openfactory;
  if (!doc || JSON.stringify(next) !== JSON.stringify(doc)) {
    if (doc) status.reloads = Number(status.reloads) + 1;
    clearScene();
    doc = next;
    buildScene(doc);
    // custom housings (PLAN 2.15) are in place before the first frame is drawn, or have failed
    const specs = new Map(doc.components.filter((g) => g.mesh).map((g) => [g.id, g.mesh!]));
    status.meshes = await attachMeshes(views, specs);
    // default models (PLAN 4.2) for every other component whose type has one
    status.housings = await attachHousings(views, new Set(specs.keys()));
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
    physics = replaying() ? null : new PlantPhysics(rapier, { components: doc.components, connections: doc.connections, friction: doc.scene.friction, dt: doc.dt });
  }
  loading = false;
}

/** The component under a viewport point: the view whose object tree holds the first hit. */
function pickComponent(clientX: number, clientY: number): { id: string; type: string } | null {
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(new THREE.Vector2((clientX / window.innerWidth) * 2 - 1, -(clientY / window.innerHeight) * 2 + 1), camera);
  const hits = raycaster.intersectObjects(views.map((v) => v.object), true);
  // Solid meshes first: an emitter's or remover's nearly transparent outline box should not hide
  // the equipment behind it; it is picked only when nothing solid is under the pointer.
  const ghost = (o: THREE.Object3D) => {
    const m = (o as THREE.Mesh).material as THREE.Material | undefined;
    return !m || (m.transparent && m.opacity < 0.2) || o instanceof THREE.LineSegments;
  };
  for (const hit of [...hits.filter((h) => !ghost(h.object)), ...hits.filter((h) => ghost(h.object))]) {
    for (let o: THREE.Object3D | null = hit.object; o; o = o.parent) {
      const view = views.find((v) => v.object === o);
      if (view) return { id: view.id, type: view.type };
    }
  }
  return null;
}

/** Where a component's origin is on screen, in CSS pixels (for tools/screenshot.py). */
function screenOf(id: string): [number, number] | null {
  const view = views.find((v) => v.id === id);
  if (!view) return null;
  const p = view.object.getWorldPosition(new THREE.Vector3()).project(camera);
  return [((p.x + 1) / 2) * window.innerWidth, ((1 - p.y) / 2) * window.innerHeight];
}

async function start(): Promise<void> {
  await loadScene();
  wireClicks();
  const menu = new FaultMenu({ target: renderer.domElement, pick: pickComponent });
  const exam = new ExamPanel(INSTRUCTOR);
  examPanel = exam;
  const refreshExam = () =>
    exam.refresh().then((on) => {
      window.__openfactory.exam = on;
    });
  await refreshExam();
  // trend panel (PLAN 3.6): any AI tag's last ten minutes; ?trend=TAG opens it on that tag
  const trend = new TrendPanel();
  if (doc) trend.setScene(doc);
  const wanted = new URLSearchParams(window.location.search).get("trend");
  if (wanted) trend.open(wanted);
  Object.assign(window, {
    __openfactoryTrend: trend,
    // this browser's own camera (PLAN 4.3: each viewer orbits independently)
    __openfactoryCamera: () => camera.position.toArray(),
    __openfactoryFaultMenu: menu,
    __openfactoryScreenOf: screenOf,
    __openfactoryPartTargets: () => parts.targets(),
  });
  try {
    rapier = await loadRapier();
    if (doc && !replaying()) physics = new PlantPhysics(rapier, { components: doc.components, connections: doc.connections, friction: doc.scene.friction, dt: doc.dt });
    window.__openfactory.physics = replaying() ? "off: replay" : `rapier ${rapier.version()}`;
  } catch (err) {
    window.__openfactory.physics = `off: ${String(err)}`; // parts follow the core positions instead
  }
  const reload = () => {
    loadScene().then(refreshExam).then(() => doc && trend.setScene(doc)).catch((err: unknown) => {
      window.__openfactory.error = String(err);
      hud.textContent = `OpenFactory: ${String(err)}`;
    });
  };
  if (INSTRUCTOR) {
    streamInstructor(currentToken(), onFrame, (reason) => {
      window.__openfactory.instructor = `refused: ${reason}`;
      hud.textContent =
        `OpenFactory · INSTRUCTOR VIEW REFUSED\n${reason}\n` +
        "Open /instructor#token=<the server's fault token>.";
    }, reload);
  } else {
    streamState(onFrame, reload);
  }
}

start().catch((err: unknown) => {
  window.__openfactory.error = String(err);
  hud.textContent = `OpenFactory: ${String(err)}`;
});

renderer.setAnimationLoop((now) => {
  const start = performance.now();
  controls.update();
  parts.animate(); // eases meshes toward their latest pose between 20 Hz frames; never steps physics
  renderer.render(scene, camera);
  labels.render(scene, camera);
  frameStats.frame(now, performance.now() - start);
  const status = window.__openfactory;
  status.frameMs = frameStats.meanMs;
  status.frameP95Ms = frameStats.p95Ms;
  status.renderMs = frameStats.renderMs;
  status.drawCalls = renderer.info.render.calls;
});
