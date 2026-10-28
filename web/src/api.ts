// Core API contract (PLAN §3.3): types for GET /api/scene and /ws/state frames, and the
// connection helpers. Mirrors src/openfactory/server/runtime.py; the scene geometry comes from
// each component's geometry() in src/openfactory/components/.

export type Vec3 = [number, number, number];

/** Conveyor types with a straight running surface (schema.CARRIER_TYPES, less the turntable). */
export const BELT_TYPES = new Set(["belt_conveyor", "roller_conveyor"]);

export interface ComponentGeometry {
  id: string;
  type: string;
  description: string;
  position: Vec3;
  rotation_deg: number;
  // type-specific dimensions (length, width, beam_length, stroke, size, ...)
  [key: string]: unknown;
}

export interface SceneDocument {
  openfactory: string;
  scene: { name: string; description: string; seed: number; friction: number };
  plc: { protocol: string; role: string };
  dt: number;
  components: ComponentGeometry[];
  connections: { from: string; to: string }[];
  tags: { name: string; kind: string; dtype: string; owner: string; description: string }[];
  warnings: string[];
}

export interface PartState {
  id: string;
  name: string;
  size: Vec3;
  position: Vec3;
  /** World velocity in m/s (belt-direction speed while carried, fall speed while falling). */
  velocity?: Vec3;
  carrier: string | null;
  /** Tick that spawned the part, and its pose then; it first moves in tick spawn_tick + 1. */
  spawn_tick?: number;
  spawn_position?: Vec3;
  /** True while the core places the part (held, against a diverter arm, or on a turning deck, PLAN §3.3). */
  follow?: boolean;
  /** Part color from the emitter's part spec (PLAN 2.7); null or absent: plain cardboard. */
  color?: string | null;
}

export interface StateFrame {
  t: number;
  tick: number;
  components: Record<string, Record<string, unknown>>;
  parts: PartState[];
  io: Record<string, boolean | number>;
}

/** Viewer status read by tools/screenshot.py (PLAN 1.13) through `window.__openfactory`. */
export interface ViewerStatus {
  ready: boolean;
  scene: string;
  components: number;
  frames: number;
  simTime: number;
  tick: number;
  error: string | null;
  [extra: string]: unknown;
}

declare global {
  interface Window {
    __openfactory: ViewerStatus;
  }
}

export async function fetchScene(): Promise<SceneDocument> {
  const res = await fetch("/api/scene");
  if (!res.ok) throw new Error(`GET /api/scene: HTTP ${res.status}`);
  return (await res.json()) as SceneDocument;
}

function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

/**
 * Stream /ws/state frames to `onFrame`, reconnecting after a second if the socket drops. When a
 * reconnected socket opens, `onReconnect` runs first: the server may have restarted, possibly with a
 * different scene, so the caller refetches /api/scene before it uses frames again.
 */
export function streamState(onFrame: (frame: StateFrame) => void, onReconnect?: () => void, reconnect = false): void {
  const ws = new WebSocket(wsUrl("/ws/state"));
  if (reconnect && onReconnect) ws.onopen = onReconnect;
  ws.onmessage = (msg) => onFrame(JSON.parse(msg.data as string) as StateFrame);
  ws.onclose = () => setTimeout(() => streamState(onFrame, onReconnect, true), 1000);
}

/** Upstream events (/ws/events): sensor reports, pushbutton presses. Queued until open. */
export class EventLink {
  private ws: WebSocket;
  private queue: string[] = [];

  constructor() {
    this.ws = new WebSocket(wsUrl("/ws/events"));
    this.ws.onopen = () => {
      for (const text of this.queue) this.ws.send(text);
      this.queue = [];
    };
  }

  send(event: Record<string, unknown>): void {
    const text = JSON.stringify(event);
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(text);
    else this.queue.push(text);
  }
}
