// OpenFactory viewer entry point.
// PLAN 1.13 stage: loads the scene from the core, draws one neutral placeholder box per component
// at its scene position, streams /ws/state into a HUD, and publishes window.__openfactory for
// tools/screenshot.py. PLAN 1.11 replaces the placeholders with per-type meshes and status colors.
import * as THREE from "three";
import { fetchScene, streamState, type ComponentGeometry, type StateFrame } from "./api";
import { cssColor, ISA101 } from "./isa101";

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

const hud = document.createElement("div");
hud.style.cssText =
  `position:absolute;top:12px;left:16px;font:14px/1.4 Arial,sans-serif;color:${cssColor("normalText")};` +
  "pointer-events:none;white-space:pre";
hud.textContent = "OpenFactory: connecting…";
app.appendChild(hud);

const scene = new THREE.Scene();
scene.background = new THREE.Color(ISA101.background);
scene.add(new THREE.HemisphereLight(0xffffff, 0x334155, 2.0));
const sun = new THREE.DirectionalLight(0xffffff, 1.5);
sun.position.set(3, 6, 4);
scene.add(sun);
const camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.1, 200);
camera.position.set(2, 4, 6);
camera.lookAt(2, 0, 0);

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

/** Rough extent of a component for its placeholder box: [size, center offset in local frame]. */
function placeholderBox(c: ComponentGeometry): [THREE.Vector3, THREE.Vector3] {
  const n = (key: string, fallback: number) => (typeof c[key] === "number" ? (c[key] as number) : fallback);
  switch (c.type) {
    case "belt_conveyor":
      return [new THREE.Vector3(n("length", 1), 0.1, n("width", 0.5)), new THREE.Vector3(n("length", 1) / 2, -0.05, 0)];
    case "photo_eye":
      return [new THREE.Vector3(0.04, 0.04, n("beam_length", 0.5)), new THREE.Vector3()];
    case "pusher":
      return [new THREE.Vector3(n("plate_width", 0.3), n("plate_height", 0.15), 0.04), new THREE.Vector3(0, 0, -0.02)];
    case "part_remover": {
      const s = (c.size as number[] | undefined) ?? [0.3, 0.3, 0.3];
      return [new THREE.Vector3(s[0], s[1], s[2]), new THREE.Vector3()];
    }
    default:
      return [new THREE.Vector3(0.15, 0.15, 0.15), new THREE.Vector3()];
  }
}

function frameCamera(boxes: THREE.Box3): void {
  const center = boxes.getCenter(new THREE.Vector3());
  const radius = boxes.getSize(new THREE.Vector3()).length() / 2;
  camera.position.copy(center).add(new THREE.Vector3(0.35, 0.8, 1.0).normalize().multiplyScalar(radius * 2.4));
  camera.lookAt(center);
}

async function start(): Promise<void> {
  const doc = await fetchScene();
  const placeholder = new THREE.MeshStandardMaterial({ color: ISA101.stopped, roughness: 0.8 });
  const bounds = new THREE.Box3();
  for (const c of doc.components) {
    const [size, offset] = placeholderBox(c);
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(size.x, size.y, size.z), placeholder);
    const group = new THREE.Group();
    group.position.set(...c.position);
    group.rotation.y = THREE.MathUtils.degToRad(c.rotation_deg);
    mesh.position.copy(offset);
    group.add(mesh);
    scene.add(group);
    bounds.expandByObject(group);
  }
  const grid = new THREE.GridHelper(20, 40, 0x3b4a5c, 0x2a3746);
  grid.position.y = -0.3;
  scene.add(grid);
  frameCamera(bounds);

  const status = window.__openfactory;
  status.scene = doc.scene.name;
  status.components = doc.components.length;
  streamState((frame: StateFrame) => {
    status.frames += 1;
    status.simTime = frame.t;
    status.tick = frame.tick;
    status.ready = true;
    hud.textContent =
      `OpenFactory · ${doc.scene.name}\n` +
      `t = ${frame.t.toFixed(2)} s   tick ${frame.tick}   parts ${frame.parts.length}`;
  });
}

start().catch((err: unknown) => {
  window.__openfactory.error = String(err);
  hud.textContent = `OpenFactory: ${String(err)}`;
});

renderer.setAnimationLoop(() => renderer.render(scene, camera));
