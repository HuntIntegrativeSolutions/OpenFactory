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
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { BELT_TYPES, EventLink, fetchScene, streamInstructor, streamState, type PartState, type SceneDocument, type StateFrame, type Vec3 } from "./api";
import { loadRapier, offDeck, PlantPhysics, tiltDeg, type Rapier } from "./physics";
import { buildComponent, floorFor, type ComponentView } from "./components";
import { attachHousings } from "./housings";
import { attachMeshes } from "./meshes";
import { cssColor, ISA101 } from "./isa101";
import { ExamPanel, examPanelTop } from "./exampanel";
import { ChecksPanel } from "./checks";
import { TrendPanel } from "./trend";
import { FaultMenu } from "./faultmenu";
import { faultOf, isInstructorPath, tokenFromHash } from "./instructor";
import { EditorPanel } from "./editor/panel";
import { recolorGizmo } from "./editor/gizmo";
import { SnapMarker } from "./editor/markers";
import { findSnap, localPort, worldPort, type Snap, type SnapPort } from "./editor/snap";
import { placeNew, type Rect } from "./editor/place";
import { currentToken, rememberToken } from "./guarded";
import { PartMeshes } from "./parts";
import { planSteps, spawnStep, strokeAt } from "./ticksync";
import { FrameStats } from "./framestats";
import { framedBounds, framePose } from "./framing";
import { applyShadowRoles, buildStage, editOverlay, Lighting } from "./lighting";
import { initialQuality, isAutomatedBrowser, QUALITIES, QUALITY_TOOLTIP, readStored, remember, type Quality } from "./quality";

/** localStorage, or null where reading it throws (blocked site data). */
function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

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
// Lighting by quality level (PLAN 4.14, web/src/lighting.ts). The environment is three's own
// RoomEnvironment (MIT, in the three package), prefiltered once, the first time a level needs it.
const lighting = new Lighting(renderer, scene, () => {
  const pmrem = new THREE.PMREMGenerator(renderer);
  const texture = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  pmrem.dispose();
  return texture;
});
let quality: Quality = initialQuality(window.location.search, readStored(storage()), isAutomatedBrowser(navigator));
lighting.apply(quality);
window.__openfactory.quality = quality;

/** The quality control (PLAN 4.14): low, standard, high; a choice made here is remembered. */
const qualityControl = document.createElement("select");
qualityControl.id = "quality";
qualityControl.title = QUALITY_TOOLTIP;
qualityControl.style.cssText =
  // bottom left: the exam and editor panels hold the top right, the trend panel the bottom right
  `position:absolute;bottom:12px;left:16px;font:12px Arial,sans-serif;color:${cssColor("normalText")};` +
  "background:#1f2937;border:1px solid #4b5563;border-radius:3px;padding:2px 4px";
for (const level of QUALITIES) qualityControl.add(new Option(`quality: ${level}`, level));
qualityControl.value = quality;
qualityControl.addEventListener("change", () => {
  quality = qualityControl.value as Quality;
  remember(storage(), quality);
  lighting.apply(quality);
  window.__openfactory.quality = quality;
});
app.appendChild(qualityControl);
const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.05, 200);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

/** Width of the editor panel the plant should be centred clear of (PLAN 4.1c), 0 outside edit
 * mode or when the window is too narrow for both. */
let panelWidth = 0;
/** How far the view's centre is shifted left for the panel, in CSS pixels (fitCamera). */
let viewShiftPx = 0;

/** Size the camera to the window; with the editor panel open, shift the view's centre left to
 * the middle of the part of the window the panel does not cover (a wider virtual view, of which
 * the window shows all but the panel's width on the left). */
function fitCamera(): void {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const p = w - panelWidth >= 320 ? panelWidth : 0;
  viewShiftPx = p;
  camera.aspect = (w + p) / h;
  if (p) camera.setViewOffset(w + p, h, p, 0, w, h);
  else camera.clearViewOffset();
  camera.updateProjectionMatrix();
  fitHud();
}

/** Keep the status lines clear of the editor panel (PLAN 4.1c fix 3): with the panel open they
 * wrap within the uncovered part of the window instead of running under it. */
function fitHud(): void {
  const panel = document.getElementById("editor-panel");
  const covered = panel ? panel.offsetWidth : 0;
  hud.style.maxWidth = covered ? `${Math.max(120, window.innerWidth - covered - 32)}px` : "";
  hud.style.whiteSpace = covered ? "pre-wrap" : "pre";
  hud.style.overflowWrap = covered ? "anywhere" : "";
}

window.addEventListener("resize", () => {
  fitCamera();
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

function buildScene(doc: SceneDocument, frame = true): void {
  const floorY = floorFor(doc.components);
  views = doc.components.map((g) => buildComponent(g, { floorY }));
  for (const view of views) {
    scene.add(view.object);
    const tag = label(view.id);
    tag.position.copy(view.labelAnchor);
    view.object.add(tag);
    labelsById.set(view.id, { label: tag, fault: null });
    applyShadowRoles(view.object);
  }
  const { floor, grid } = buildStage(floorY);
  scene.add(floor, grid);
  plant = [...views.map((v) => v.object), floor, grid];
  const bounds = framedBounds(views.map((v) => v.object));
  if (frame) frameCamera(bounds);
  // the shadow camera covers the plant down to the floor its shadows land on (PLAN 4.14), after
  // every rebuild, edits included, even when the camera stays where the user left it
  lighting.fit(bounds.clone().expandByPoint(bounds.getCenter(new THREE.Vector3()).setY(floorY)));
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
    if (!view || !doc || !events || editing()) return;
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
  placeChecks();
  stateCost.frame(start, performance.now() - start);
  status.stateMs = stateCost.renderMs; // main-thread ms per state frame: scene updates and physics
}

const stateCost = new FrameStats(40);
let examPanel: ExamPanel | null = null; // set in start(); synced from every state frame
let checksPanel: ChecksPanel | null = null; // the live checks (PLAN 4.1i), set in start()

/** The checks panel at the top right: below the exam panel when that is shown, else below the
 * status lines on a narrow screen, as the exam panel is placed. */
function placeChecks(): void {
  if (!checksPanel || checksPanel.element.style.display === "none") return;
  const exam = examPanel?.element;
  const top = exam && exam.style.display !== "none"
    ? exam.getBoundingClientRect().bottom + 8
    : examPanelTop(hud.getBoundingClientRect(), checksPanel.element.offsetWidth, window.innerWidth);
  checksPanel.placeAt(top);
}

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
    `draw calls ${renderer.info.render.calls}   quality ${quality}`;
}

/**
 * Fetch /api/scene and (re)build: the plant only if the document changed, and always a fresh
 * physics world, part set, tick sync, and event link, because a reconnect may follow a server
 * restart (ticks and part ids start again).
 */
/** A recording served by `openfactory replay` (PLAN 2.12): parts are drawn where it has them,
 * with no viewer physics, as when Rapier fails to load. */
/** The scene editor (PLAN 4.1, `openfactory edit`): no viewer physics, clicks select. */
function editing(): boolean {
  return doc?.mode === "edit";
}

function replaying(): boolean {
  return doc?.mode === "replay";
}

async function loadScene(): Promise<void> {
  await applyScene(await fetchScene());
}

/** Build the plant from a scene document (a fetched one, or an editor preview's). */
async function applyScene(next: SceneDocument): Promise<void> {
  loading = true;
  const status = window.__openfactory;
  if (!doc || JSON.stringify(next) !== JSON.stringify(doc)) {
    // an editor preview redraws the plant where the user is looking (PLAN 4.1c)
    const keepCamera = doc !== null && editing() && next.mode === "edit";
    if (doc) status.reloads = Number(status.reloads) + 1;
    clearScene();
    doc = next;
    buildScene(doc, !keepCamera);
    // custom housings (PLAN 2.15) are in place before the first frame is drawn, or have failed
    const specs = new Map(doc.components.filter((g) => g.mesh).map((g) => [g.id, g.mesh!]));
    status.meshes = await attachMeshes(views, specs);
    // default models (PLAN 4.2) for every other component whose type has one
    status.housings = await attachHousings(views, new Set(specs.keys()));
    // housings and custom meshes arrived after buildScene set the roles: set them again (PLAN 4.14)
    for (const view of views) applyShadowRoles(view.object);
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
  if (rapier && !editing()) {
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

/** Where a component's origin, or `local` in its frame (PLAN 4.14 pixel test), is on screen, in
 * CSS pixels (for tools/screenshot.py and the integration tests). */
function screenOf(id: string, local?: [number, number, number]): [number, number] | null {
  const view = views.find((v) => v.id === id);
  if (!view) return null;
  const world = local ? view.object.localToWorld(new THREE.Vector3(...local)) : view.object.getWorldPosition(new THREE.Vector3());
  const p = world.project(camera);
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
    // the component under a viewport point, for tests that click one (PLAN 4.1b)
    __openfactoryPick: (x: number, y: number) => pickComponent(x, y)?.id ?? null,
    __openfactoryPartTargets: () => parts.targets(),
  });
  if (editing()) {
    await startEditor();
    streamState(onFrame, () => void 0);
    return;
  }
  // live checks (PLAN 4.1i): read-only, the trainee's view and the instructor's alike
  checksPanel = new ChecksPanel(INSTRUCTOR);
  checksPanel.start();
  Object.assign(window, { __openfactoryChecks: checksPanel });
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

/** Edit mode (PLAN 4.1b, 4.1c): the editor panel, click to select, a box around the selection,
 * and a gizmo that moves the selection on the floor or turns it about the vertical axis, on a
 * 0.05 m and 15 degree grid that Shift turns off. A composite instance moves as one: the gizmo
 * holds a handle at the instance's own position, and its parts follow it while dragging. */
async function startEditor(): Promise<void> {
  let selectedId: string | null = null;
  let outline: THREE.BoxHelper | THREE.Box3Helper | null = null;
  let suppressClick = false;
  const handle = new THREE.Object3D();
  scene.add(handle);
  const gizmo = new TransformControls(camera, renderer.domElement);
  recolorGizmo(gizmo); // no status color on any handle (PLAN 4.1c fix 2, editor/gizmo.ts)
  scene.add(editOverlay(gizmo.getHelper())); // no shadows from the gizmo (PLAN 4.14)
  const GRID_M = 0.05;
  const GRID_RAD = THREE.MathUtils.degToRad(15);
  const snap = (on: boolean) => {
    gizmo.setTranslationSnap(on ? GRID_M : null);
    gizmo.setRotationSnap(on ? GRID_RAD : null);
  };
  snap(true);
  window.addEventListener("keydown", (e) => e.key === "Shift" && snap(false));
  window.addEventListener("keyup", (e) => e.key === "Shift" && snap(true));
  const setMode = (mode: "move" | "turn") => {
    gizmo.setMode(mode === "move" ? "translate" : "rotate");
    gizmo.showX = mode === "move";
    gizmo.showY = mode === "turn";
    gizmo.showZ = mode === "move";
  };
  /** The views drawn for a component in the file: itself, or a composite instance's parts. */
  const boundsOf = (objects: THREE.Object3D[]) => {
    const box = new THREE.Box3();
    for (const o of objects) box.expandByObject(o);
    return box;
  };
  const viewsOf = (id: string) =>
    views.filter((v) => {
      if (v.id === id) return true;
      const g = doc?.components.find((c) => c.id === v.id);
      return (g?.composite as { id?: string } | undefined)?.id === id;
    });
  let dragStart: { handle: THREE.Matrix4; objects: [THREE.Object3D, THREE.Matrix4][] } | null = null;
  // snap ports (PLAN 4.1d): a conveyor dragged near another's end snaps to it on release
  const marker = new SnapMarker();
  scene.add(marker.object);
  let candidate: Snap | null = null;
  const snapFor = (): Snap | null => {
    if (!selectedId || !doc || gizmo.mode !== "translate") return null;
    const mine = doc.components.find((c) => c.id === selectedId);
    const ports = mine?.snaps as SnapPort[] | undefined;
    if (!mine || !ports?.length) return null; // not a conveyor (a composite instance moves as one)
    const others = doc.components
      .filter((c) => c.id !== selectedId && Array.isArray(c.snaps) && (c.snaps as SnapPort[]).length)
      .map((c) => ({ id: c.id, ports: c.snaps as SnapPort[] }));
    const published = { position: mine.position as [number, number, number], rotation_deg: mine.rotation_deg };
    const now = { position: [handle.position.x, handle.position.y, handle.position.z] as [number, number, number], rotation_deg: THREE.MathUtils.radToDeg(handle.rotation.y) };
    return findSnap(selectedId, ports, published, now, others);
  };
  const highlight = () => {
    if (outline) scene.remove(outline);
    outline = null;
    gizmo.detach();
    const drawn = selectedId ? viewsOf(selectedId) : [];
    const data = selectedId ? panel.componentData(selectedId) : null;
    if (!drawn.length) return;
    outline = editOverlay(drawn.length === 1
      ? new THREE.BoxHelper(drawn[0].object, new THREE.Color(cssColor("highlight")))
      : new THREE.Box3Helper(boundsOf(drawn.map((v) => v.object)), new THREE.Color(cssColor("highlight"))));
    // ISA-101 highlight at its exact color at every quality level; a line casts no shadow (PLAN 4.14)
    (outline.material as THREE.LineBasicMaterial).toneMapped = false;
    scene.add(outline);
    const position = data?.position as number[] | undefined;
    if (!position || drawn.some((v) => v.type === "pipe")) return; // a pipe is drawn from its path
    handle.position.set(position[0], position[1], position[2]);
    handle.rotation.set(0, THREE.MathUtils.degToRad(Number(data?.rotation_deg ?? 0)), 0);
    handle.updateMatrixWorld();
    gizmo.attach(handle);
  };
  const select = (id: string | null) => {
    selectedId = id;
    panel.select(id);
    highlight();
  };
  /** The floor point (y 0, the plant's datum) at the centre of the uncovered view. */
  const viewCentre = (): THREE.Vector3 => {
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(-viewShiftPx / window.innerWidth, 0), camera);
    const hit = ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3());
    return hit ?? controls.target.clone();
  };
  const placement = (): [number, number, number] => {
    const at = viewCentre();
    const round = (v: number) => Math.round(v / GRID_M) * GRID_M;
    return [Number(round(at.x).toFixed(3)), 0, Number(round(at.z).toFixed(3))];
  };
  /** Each file component's drawn footprint in the floor plane (a composite's parts as one). */
  const footprints = (): Record<string, Rect> => {
    const out: Record<string, Rect> = {};
    for (const v of views) {
      const g = doc?.components.find((c) => c.id === v.id);
      const id = (g?.composite as { id?: string } | undefined)?.id ?? v.id;
      const box = boundsOf([v.object]);
      if (!Number.isFinite(box.min.x)) continue;
      const r = out[id];
      out[id] = r
        ? { minX: Math.min(r.minX, box.min.x), minZ: Math.min(r.minZ, box.min.z), maxX: Math.max(r.maxX, box.max.x), maxZ: Math.max(r.maxZ, box.max.z) }
        : { minX: box.min.x, minZ: box.min.z, maxX: box.max.x, maxZ: box.max.z };
    }
    return out;
  };
  const panel = new EditorPanel({
    onScene: (next) => applyScene(next).then(highlight),
    onSelect: select,
    placement,
    setMode,
    drawn: (id) => viewsOf(id).length > 0,
    settle: (id, position, rotationDeg) => {
      // PLAN 4.1e: the footprints (x, z) of what is drawn: the new component's, and every
      // other's, each with the id in the file it belongs to (a composite part's instance)
      const mine = viewsOf(id);
      if (!mine.length || !doc || mine.some((v) => v.type === "pipe")) return null; // a pipe runs from its ends
      const rectOf = (box: THREE.Box3): Rect => ({ minX: box.min.x, minZ: box.min.z, maxX: box.max.x, maxZ: box.max.z });
      const fileId = (viewId: string) => {
        const g = doc?.components.find((c) => c.id === viewId);
        return (g?.composite as { id?: string } | undefined)?.id ?? viewId;
      };
      const obstacles = views
        .filter((v) => !mine.includes(v))
        .map((v) => ({ id: fileId(v.id), rect: rectOf(boundsOf([v.object])) }))
        .filter((o) => Number.isFinite(o.rect.minX));
      const geometry = doc.components.find((c) => c.id === id);
      const conveyors = doc.components
        .filter((c) => c.id !== id && Array.isArray(c.snaps) && (c.snaps as SnapPort[]).length)
        .map((c) => ({ id: c.id, ports: c.snaps as SnapPort[] }));
      return placeNew(
        { id, position, rotation_deg: rotationDeg, rect: rectOf(boundsOf(mine.map((v) => v.object))), ports: (geometry?.snaps as SnapPort[] | undefined) ?? [] },
        obstacles,
        conveyors,
      );
    },
  });
  window.__openfactory.editor = panel.status;
  window.__openfactory.physics = "off: edit";
  gizmo.addEventListener("dragging-changed", (e) => {
    const dragging = Boolean((e as unknown as { value: boolean }).value);
    controls.enabled = !dragging;
    if (dragging) {
      handle.updateMatrixWorld();
      dragStart = {
        handle: handle.matrixWorld.clone(),
        objects: (selectedId ? viewsOf(selectedId) : []).map((v) => [v.object, v.object.matrix.clone()]),
      };
      return;
    }
    suppressClick = true; // the click that ends a drag does not change the selection
    if (!selectedId || !dragStart) return;
    dragStart = null;
    marker.hide();
    panel.status.snapping = null;
    const snapped = candidate;
    candidate = null;
    if (snapped) {
      // the ends meet within 1 mm and face within 0.1 degrees: keep 0.1 mm and 0.0001 degrees
      const at = snapped.pose.position.map((v) => Number(v.toFixed(4))) as [number, number, number];
      const deg = Number(snapped.pose.rotation_deg.toFixed(4));
      panel.status.snap = { id: selectedId, mine: snapped.mine, theirs: snapped.theirs.id, port: snapped.theirs.port, connection: snapped.connection };
      panel.snapTo(selectedId, at, deg, snapped.connection);
      return;
    }
    const p = handle.position;
    const position: [number, number, number] = [p.x, p.y, p.z].map((v) => Number(v.toFixed(3))) as [number, number, number];
    let deg = THREE.MathUtils.radToDeg(handle.rotation.y);
    deg = Number((((deg % 360) + 540) % 360 - 180).toFixed(1));
    const before = Number(panel.componentData(selectedId)?.rotation_deg ?? 0);
    panel.moveTo(selectedId, position, Math.abs(deg - before) > 1e-6 ? deg : null);
  });
  gizmo.addEventListener("objectChange", () => {
    if (!dragStart) return;
    // move the drawn views with the handle until the preview redraws them
    handle.updateMatrixWorld();
    const delta = handle.matrixWorld.clone().multiply(dragStart.handle.clone().invert());
    for (const [object, start] of dragStart.objects) {
      object.matrix.copy(delta.clone().multiply(start));
      object.matrix.decompose(object.position, object.quaternion, object.scale);
    }
    if (outline instanceof THREE.BoxHelper) outline.update();
    else if (outline) outline.box.copy(boundsOf(dragStart.objects.map(([o]) => o)));
    candidate = snapFor();
    panel.status.snapping = candidate ? `${candidate.mine} -> ${candidate.theirs.id}.${candidate.theirs.port}` : null;
    if (!candidate) {
      marker.hide();
      return;
    }
    const mine = doc!.components.find((c) => c.id === selectedId)!;
    const local = localPort((mine.snaps as SnapPort[]).find((p) => p.name === candidate!.mine)!, { position: mine.position as [number, number, number], rotation_deg: mine.rotation_deg });
    const now = worldPort(local, { position: [handle.position.x, handle.position.y, handle.position.z], rotation_deg: THREE.MathUtils.radToDeg(handle.rotation.y) });
    marker.show(candidate.theirs.position, now.position);
  });
  renderer.domElement.addEventListener("click", (e) => {
    if (suppressClick) {
      suppressClick = false;
      return;
    }
    const picked = pickComponent(e.clientX, e.clientY);
    // a part expanded from a composite (PLAN 2.16) selects its instance, which is what the file holds
    const geometry = picked ? doc?.components.find((c) => c.id === picked.id) : undefined;
    const instance = (geometry?.composite as { id?: string } | undefined)?.id;
    select(picked ? (instance ?? picked.id) : null);
  });
  window.addEventListener("keydown", (e) => {
    const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement;
    if (!typing && e.key === "Delete") void panel.deleteSelected();
  });
  Object.assign(window, {
    /** Pan the view so Add drops at floor point (x, z) (tests, PLAN 4.1e). */
    __openfactoryEditorLook: (x: number, z: number) => {
      const at = viewCentre();
      const shift = new THREE.Vector3(x - at.x, 0, z - at.z);
      camera.position.add(shift);
      controls.target.add(shift);
      controls.update();
      return placement();
    },
    /** Each component's drawn footprint (tests, PLAN 4.1e). */
    __openfactoryEditorFootprints: footprints,
    /** A component's values as the file with pending edits has them (tests, PLAN 4.1c). */
    __openfactoryEditorData: (id: string) => panel.componentData(id),
    /** Select a component as a click on it would (tests, PLAN 4.1c). */
    __openfactoryEditorSelect: (id: string | null) => select(id),
    /** Where the gizmo's handles are on screen, for tests that drag them (PLAN 4.1c): the
     * handle's origin, a point on the x arrow (move) and on the vertical-axis ring (turn). */
    __openfactoryGizmo: () => {
      if (!gizmo.object) return null;
      const origin = handle.getWorldPosition(new THREE.Vector3());
      const fov = camera.fov;
      const factor = origin.distanceTo(camera.position) * Math.min((1.9 * Math.tan((Math.PI * fov) / 360)) / camera.zoom, 7);
      const unit = (factor * gizmo.size) / 4;
      const along = (v: THREE.Vector3) => {
        const q = origin.clone().add(v.multiplyScalar(unit)).project(camera);
        return [((q.x + 1) / 2) * window.innerWidth, ((1 - q.y) / 2) * window.innerHeight];
      };
      return { origin: along(new THREE.Vector3()), xArrow: along(new THREE.Vector3(0.6, 0, 0)), ring: along(new THREE.Vector3(0, 0, 0.5)), unitPx: unit };
    },
  });
  panelWidth = panel.element.offsetWidth;
  fitCamera();
  await panel.start();
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
