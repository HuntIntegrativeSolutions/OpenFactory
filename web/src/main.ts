// OpenFactory viewer (PLAN 1.11): loads the scene from the core, builds one mesh per component from
// its type's builder (web/src/components/), streams /ws/state into component status, parts, and a
// HUD, and publishes window.__openfactory for tools/screenshot.py (PLAN 1.13). Orbit with the
// mouse; click a pushbutton to press it or a selector to turn it. Status colors are ISA-101
// (green running/active, grey stopped/idle). Geometry comes only from the scene JSON.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { EventLink, fetchScene, streamState, type SceneDocument, type StateFrame } from "./api";
import { buildComponent, floorFor, type ComponentView } from "./components";
import { cssColor, ISA101 } from "./isa101";
import { PartMeshes } from "./parts";

window.__openfactory = {
  ready: false,
  scene: "",
  components: 0,
  frames: 0,
  simTime: 0,
  tick: 0,
  error: null,
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

function onFrame(doc: SceneDocument, frame: StateFrame): void {
  lastFrame = frame;
  for (const view of views) view.update(frame.components[view.id] ?? {});
  parts.update(frame.parts);
  const status = window.__openfactory;
  status.frames += 1;
  status.simTime = frame.t;
  status.tick = frame.tick;
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
  wireClicks(doc, new EventLink());
  streamState((frame) => onFrame(doc, frame));
}

start().catch((err: unknown) => {
  window.__openfactory.error = String(err);
  hud.textContent = `OpenFactory: ${String(err)}`;
});

renderer.setAnimationLoop(() => {
  controls.update();
  parts.animate();
  renderer.render(scene, camera);
  labels.render(scene, camera);
});
