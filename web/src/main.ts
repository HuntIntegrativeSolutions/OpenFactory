// OpenFactory viewer entry point (Milestone 0 scaffold).
// Renders an empty scene on the ISA-101 dark background and lazy-loads Rapier so the
// build proves both dependencies bundle. Scene loading arrives in PLAN task 1.11.
import * as THREE from "three";

const ISA101_BACKGROUND = 0x1f2a36;

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("#app element missing from index.html");

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
app.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(ISA101_BACKGROUND);
const camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.1, 200);
camera.position.set(4, 3, 6);
camera.lookAt(0, 0, 0);

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

renderer.setAnimationLoop(() => renderer.render(scene, camera));

// Rapier's -compat build embeds ~4 MB of WASM; load it on demand, never on first paint.
void import("@dimforge/rapier3d-compat").then(async (RAPIER) => {
  await RAPIER.init();
  console.info(`OpenFactory viewer: three r${THREE.REVISION}, rapier ${RAPIER.version()}`);
});
