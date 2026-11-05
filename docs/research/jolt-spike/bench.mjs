// PLAN 2.9 Jolt spike (docs/research/jolt-vs-rapier.md): the same conveyor setups in Rapier (as
// web/src/physics.ts builds them, without its 2.9 seam fix) and Jolt (static belts, conveyor
// surface velocity from the contact listener, as Jolt's own conveyor sample). Prints JSON lines.
// Not part of the build and not a dependency: run it in a scratch folder,
//   npm init -y && npm i jolt-physics@1.1.0 @dimforge/rapier3d-compat@0.21.0
//   node bench.mjs                                         # everything, both engines
//   SEAM_ONLY=1 JBELT_R=0.005 JBOX_R=0 node bench.mjs      # Jolt seam and queue at radii (m)
import RAPIER from "@dimforge/rapier3d-compat";
import initJolt from "jolt-physics/wasm-compat";

const DT = 0.02;
const MU = 0.8;
const G = 9.81;
const BELT_T = 0.05; // half thickness
const R_EDGE = 0.005;

// ---------------------------------------------------------------- Rapier, as physics.ts
async function rapierWorld() {
  await RAPIER.init();
  const world = new RAPIER.World({ x: 0, y: -G, z: 0 });
  world.timestep = DT;
  const belts = [];
  return {
    name: "rapier",
    addBelt(x0, z, length, width, speed) {
      const home = { x: x0 + length / 2, y: -BELT_T, z };
      const body = world.createRigidBody(RAPIER.RigidBodyDesc.kinematicVelocityBased().setTranslation(home.x, home.y, home.z));
      world.createCollider(RAPIER.ColliderDesc.roundCuboid(length / 2 - R_EDGE, BELT_T - R_EDGE, width / 2 - R_EDGE, R_EDGE).setFriction(MU).setRestitution(0), body);
      belts.push({ body, home, speed });
    },
    addWall(x, z, width, height) {
      const body = world.createRigidBody(RAPIER.RigidBodyDesc.fixed().setTranslation(x + 0.01, height / 2, z));
      world.createCollider(RAPIER.ColliderDesc.cuboid(0.01, height / 2, width / 2).setFriction(0), body);
    },
    addBox(x, y, z, size, vx = 0) {
      const body = world.createRigidBody(RAPIER.RigidBodyDesc.dynamic().setTranslation(x, y, z).setLinvel(vx, 0, 0));
      world.createCollider(RAPIER.ColliderDesc.cuboid(size[0] / 2, size[1] / 2, size[2] / 2).setFriction(MU).setRestitution(0), body);
      return body;
    },
    step() {
      for (const b of belts) {
        b.body.setTranslation(b.home, true);
        b.body.setLinvel({ x: b.speed, y: 0, z: 0 }, true);
      }
      world.step();
    },
    pose(body) {
      const t = body.translation();
      const q = body.rotation();
      return { x: t.x, y: t.y, z: t.z, tilt: (Math.acos(Math.min(1, Math.max(-1, 1 - 2 * (q.x * q.x + q.z * q.z)))) * 180) / Math.PI };
    },
    free() { world.free(); },
  };
}

// ---------------------------------------------------------------- Jolt
let Jolt;
async function joltWorld() {
  Jolt ??= await initJolt();
  const settings = new Jolt.JoltSettings();
  const LAYER_STATIC = 0, LAYER_MOVING = 1;
  const objectFilter = new Jolt.ObjectLayerPairFilterTable(2);
  objectFilter.EnableCollision(LAYER_STATIC, LAYER_MOVING);
  objectFilter.EnableCollision(LAYER_MOVING, LAYER_MOVING);
  const bp = new Jolt.BroadPhaseLayerInterfaceTable(2, 2);
  bp.MapObjectToBroadPhaseLayer(LAYER_STATIC, new Jolt.BroadPhaseLayer(0));
  bp.MapObjectToBroadPhaseLayer(LAYER_MOVING, new Jolt.BroadPhaseLayer(1));
  settings.mObjectLayerPairFilter = objectFilter;
  settings.mBroadPhaseLayerInterface = bp;
  settings.mObjectVsBroadPhaseLayerFilter = new Jolt.ObjectVsBroadPhaseLayerFilterTable(settings.mBroadPhaseLayerInterface, 2, settings.mObjectLayerPairFilter, 2);
  const jolt = new Jolt.JoltInterface(settings);
  Jolt.destroy(settings);
  const system = jolt.GetPhysicsSystem();
  system.SetGravity(new Jolt.Vec3(0, -G, 0));
  const bi = system.GetBodyInterface();
  const beltSpeed = new Map(); // body id index -> speed along +x
  const listener = new Jolt.ContactListenerJS();
  const surface = (b1p, b2p, settingsP) => {
    const b1 = Jolt.wrapPointer(b1p, Jolt.Body);
    const b2 = Jolt.wrapPointer(b2p, Jolt.Body);
    const s1 = beltSpeed.get(b1.GetID().GetIndexAndSequenceNumber());
    const s2 = beltSpeed.get(b2.GetID().GetIndexAndSequenceNumber());
    if (s1 === undefined && s2 === undefined) return;
    const cs = Jolt.wrapPointer(settingsP, Jolt.ContactSettings);
    // relative surface velocity of body 2 with respect to body 1 (Jolt's conveyor sample)
    const v = s1 !== undefined ? -s1 : s2;
    cs.mRelativeLinearSurfaceVelocity = new Jolt.Vec3(v, 0, 0);
  };
  listener.OnContactValidate = () => Jolt.ValidateResult_AcceptAllContactsForThisBodyPair;
  listener.OnContactAdded = (b1, b2, _m, s) => surface(b1, b2, s);
  listener.OnContactPersisted = (b1, b2, _m, s) => surface(b1, b2, s);
  listener.OnContactRemoved = () => {};
  system.SetContactListener(listener);
  const make = (shape, x, y, z, motion, layer, friction) => {
    const cs = new Jolt.BodyCreationSettings(shape, new Jolt.RVec3(x, y, z), new Jolt.Quat(0, 0, 0, 1), motion, layer);
    cs.mFriction = friction;
    cs.mRestitution = 0;
    const body = bi.CreateBody(cs);
    Jolt.destroy(cs);
    bi.AddBody(body.GetID(), Jolt.EActivation_Activate);
    return body;
  };
  return {
    name: "jolt",
    addBelt(x0, z, length, width, speed) {
      const shape = new Jolt.BoxShape(new Jolt.Vec3(length / 2, BELT_T, width / 2), Number(process.env.JBELT_R ?? R_EDGE), null);
      const body = make(shape, x0 + length / 2, -BELT_T, z, Jolt.EMotionType_Static, LAYER_STATIC, MU);
      beltSpeed.set(body.GetID().GetIndexAndSequenceNumber(), speed);
    },
    addWall(x, z, width, height) {
      const shape = new Jolt.BoxShape(new Jolt.Vec3(0.01, height / 2, width / 2), 0.0, null);
      make(shape, x + 0.01, height / 2, z, Jolt.EMotionType_Static, LAYER_STATIC, 0);
    },
    addBox(x, y, z, size, vx = 0) {
      const shape = new Jolt.BoxShape(new Jolt.Vec3(size[0] / 2, size[1] / 2, size[2] / 2), Number(process.env.JBOX_R ?? 0), null);
      const body = make(shape, x, y, z, Jolt.EMotionType_Dynamic, LAYER_MOVING, MU);
      body.SetLinearVelocity(new Jolt.Vec3(vx, 0, 0));
      return body;
    },
    step() { jolt.Step(DT, 1); },
    pose(body) {
      const t = body.GetPosition();
      const q = body.GetRotation();
      const qx = q.GetX(), qz = q.GetZ();
      return { x: t.GetX(), y: t.GetY(), z: t.GetZ(), tilt: (Math.acos(Math.min(1, Math.max(-1, 1 - 2 * (qx * qx + qz * qz)))) * 180) / Math.PI };
    },
    free() { Jolt.destroy(jolt); },
  };
}

const engines = { rapier: rapierWorld, jolt: joltWorld };

// ---------------------------------------------------------------- the setups
async function ramp(make) {
  // a box at rest on a 0.5 m/s belt: ticks to 99 % of belt speed (core: v/(mu g) = 3.19 -> 4)
  const w = await make();
  w.addBelt(0, 0, 4, 0.5, 0.5);
  const box = w.addBox(1.0, 0.1, 0, [0.3, 0.2, 0.3]);
  let x = w.pose(box).x;
  for (let k = 1; k < 200; k++) {
    w.step();
    const now = w.pose(box).x;
    if ((now - x) / DT >= 0.495) { w.free(); return k; }
    x = now;
  }
  w.free();
  return null;
}

async function seam(make) {
  // two 0.5 m/s belts end to end; a box riding at speed crosses: lag behind ideal (core: 0)
  const w = await make();
  w.addBelt(0, 0, 2, 0.5, 0.5);
  w.addBelt(2, 0, 2, 0.5, 0.5);
  const box = w.addBox(1.0, 0.1, 0, [0.3, 0.2, 0.3], 0.5);
  for (let k = 0; k < 20; k++) w.step();
  const x0 = w.pose(box).x;
  for (let k = 0; k < 150; k++) w.step();
  const lag = x0 + 0.5 * DT * 150 - w.pose(box).x;
  w.free();
  return lag;
}

async function queue(make) {
  // five boxes ride a 0.5 m/s belt into a fixed stop at x 3.0 and queue nose to tail
  // (core: centres 2.85, 2.55, 2.25, 1.95, 1.65; upright); after 10 s
  const w = await make();
  w.addBelt(0, 0, 4, 0.5, 0.5);
  w.addWall(3.0, 0, 0.5, 0.15);
  const boxes = [0.2, 0.55, 0.9, 1.25, 1.6].map((x) => w.addBox(x, 0.1, 0, [0.3, 0.2, 0.3]));
  for (let k = 0; k < 500; k++) w.step();
  const poses = boxes.map((b) => w.pose(b)).sort((a, b) => b.x - a.x);
  const ideal = [2.85, 2.55, 2.25, 1.95, 1.65];
  const err = Math.max(...poses.map((p, i) => Math.abs(p.x - ideal[i])));
  const tilt = Math.max(...poses.map((p) => p.tilt));
  w.free();
  return { worstErrMm: +(err * 1000).toFixed(2), worstTiltDeg: +tilt.toFixed(2) };
}

async function load200(make) {
  // ten 13 m lanes at 0.5 m/s, 20 boxes each (200), a wall at each end: ms per step
  const w = await make();
  for (let lane = 0; lane < 10; lane++) {
    const z = lane * 0.8;
    w.addBelt(0, z, 13, 0.5, 0.5);
    w.addWall(13, z, 0.5, 0.15);
    for (let i = 0; i < 20; i++) w.addBox(0.3 + i * 0.6, 0.1, z, [0.3, 0.2, 0.3]);
  }
  for (let k = 0; k < 200; k++) w.step(); // ramp up, some queue at the wall
  const times = [];
  for (let k = 0; k < 1000; k++) {
    const t0 = performance.now();
    w.step();
    times.push(performance.now() - t0);
  }
  w.free();
  times.sort((a, b) => a - b);
  return { meanMs: +(times.reduce((a, b) => a + b, 0) / times.length).toFixed(3), p99Ms: +times[Math.floor(0.99 * times.length)].toFixed(3) };
}

if (process.env.SEAM_ONLY) {
  console.log(JSON.stringify({ belt: process.env.JBELT_R, box: process.env.JBOX_R, seamLagMm: +((await seam(joltWorld)) * 1000).toFixed(2), queue: await queue(joltWorld) }));
  process.exit(0);
}
for (const [name, make] of Object.entries(engines)) {
  const out = { engine: name };
  out.rampTicks = await ramp(make);
  out.seamLagMm = +((await seam(make)) * 1000).toFixed(2);
  out.queue = await queue(make);
  out.load200 = [await load200(make), await load200(make), await load200(make)];
  console.log(JSON.stringify(out));
}
