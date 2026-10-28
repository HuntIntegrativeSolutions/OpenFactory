// Fault injection menu (PLAN 2.6): right-click a component (or long-press it on a touch screen) to
// inject one of the faults its type supports, or to clear the active one. The menu reads
// GET /api/fault (active faults and each component's supported kinds) when it opens and sends
// POST /api/fault or DELETE /api/fault/{id}; the core applies it from the next tick, and the
// component's label shows the fault in ISA-101 alarm yellow once a state frame carries it.
//
// A server bound beyond loopback guards /api/fault with a token (PLAN §6.4): on a 401 the menu
// asks for it once, keeps it for the browser tab (sessionStorage), and sends it in the
// X-OpenFactory-Fault-Token header; a 403 means the server has the fault API off.
//
// OrbitControls pans on a right-button drag, so the menu opens only when the right button moved no
// more than CLICK_SLOP_PX between press and release; a long-press is 600 ms with no more than
// TOUCH_SLOP_PX of movement.
import { cssColor } from "./isa101";

const CLICK_SLOP_PX = 5;
const TOUCH_SLOP_PX = 10;
const LONG_PRESS_MS = 600;
const TOKEN_HEADER = "X-OpenFactory-Fault-Token";
const TOKEN_KEY = "openfactory.faultToken";

function storedToken(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // storage blocked: ask again next time
  }
}

function storeToken(token: string | null): void {
  try {
    if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
    else window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage blocked: the token lives only in this menu instance
  }
}

interface FaultList {
  active: { id: string; kind: string; params: Record<string, unknown>; tick: number }[];
  supported: Record<string, string[]>;
}

export interface FaultMenuOptions {
  /** Element that receives pointer events (the renderer canvas). */
  target: HTMLElement;
  /** Component id under a viewport point, or null. */
  pick(clientX: number, clientY: number): { id: string; type: string } | null;
}

export class FaultMenu {
  readonly element: HTMLDivElement;
  private down: { x: number; y: number } | null = null;
  private press: { x: number; y: number; timer: number } | null = null;
  private readonly options: FaultMenuOptions;
  private token: string | null = storedToken();

  constructor(options: FaultMenuOptions) {
    this.options = options;
    this.element = document.createElement("div");
    this.element.id = "fault-menu";
    this.element.style.cssText =
      `position:absolute;display:none;z-index:10;min-width:180px;font:13px Arial,sans-serif;` +
      `color:${cssColor("normalText")};background:#111827;border:1px solid #4b5563;border-radius:4px;` +
      "box-shadow:0 4px 12px rgba(0,0,0,0.5);padding:4px 0";
    document.body.appendChild(this.element);
    const { target } = options;
    target.addEventListener("pointerdown", (e) => {
      if (e.button === 2) this.down = { x: e.clientX, y: e.clientY };
      if (e.pointerType === "touch") {
        const timer = window.setTimeout(() => void this.openAt(e.clientX, e.clientY), LONG_PRESS_MS);
        this.press = { x: e.clientX, y: e.clientY, timer };
      }
      if (!this.element.contains(e.target as Node)) this.close();
    });
    target.addEventListener("pointermove", (e) => {
      if (this.press && Math.hypot(e.clientX - this.press.x, e.clientY - this.press.y) > TOUCH_SLOP_PX) this.cancelPress();
    });
    target.addEventListener("pointerup", () => this.cancelPress());
    target.addEventListener("pointercancel", () => this.cancelPress());
    target.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const still = this.down && Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) <= CLICK_SLOP_PX;
      this.down = null;
      if (still) void this.openAt(e.clientX, e.clientY);
    });
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") this.close();
    });
  }

  private cancelPress(): void {
    if (this.press) window.clearTimeout(this.press.timer);
    this.press = null;
  }

  close(): void {
    this.element.style.display = "none";
    this.element.replaceChildren();
  }

  /** fetch() on the fault API with the token, asking for it once if the server answers 401. */
  private async call(url: string, init: RequestInit = {}): Promise<Response> {
    const send = () => {
      const headers = new Headers(init.headers);
      if (this.token) headers.set(TOKEN_HEADER, this.token);
      return fetch(url, { ...init, headers });
    };
    let res = await send();
    if (res.status === 401) {
      const entered = window.prompt("This server needs its fault token to inject faults:");
      this.token = entered ? entered.trim() : null;
      storeToken(this.token);
      if (this.token) res = await send();
    }
    return res;
  }

  /** Open the menu for the component under a viewport point (exposed for the screenshot runner). */
  async openAt(clientX: number, clientY: number): Promise<string | null> {
    const hit = this.options.pick(clientX, clientY);
    this.close();
    if (!hit) return null;
    const res = await this.call("/api/fault");
    if (!res.ok) {
      if (res.status === 401) storeToken((this.token = null)); // wrong token: ask again next time
      this.add(`${hit.id} · ${hit.type}`, null, true);
      this.add(res.status === 403 ? "Fault injection is off on this server" : "Fault token not accepted", null);
      this.show(clientX, clientY);
      return hit.id;
    }
    const faults = (await res.json()) as FaultList;
    const kinds = faults.supported[hit.id] ?? [];
    const active = faults.active.find((f) => f.id === hit.id);
    this.add(`${hit.id} · ${hit.type}`, null, true);
    if (!kinds.length) this.add(`No faults for a ${hit.type}`, null);
    for (const kind of kinds) {
      this.add(`${kind === active?.kind ? "● " : ""}${kind.replace("_", " ")}`, () =>
        this.call("/api/fault", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: hit.id, kind }),
        }),
      );
    }
    if (active) this.add(`Clear fault (${active.kind.replace("_", " ")})`, () => this.call(`/api/fault/${hit.id}`, { method: "DELETE" }));
    this.show(clientX, clientY);
    return hit.id;
  }

  private show(clientX: number, clientY: number): void {
    const { style } = this.element;
    style.display = "block";
    style.left = `${Math.min(clientX, window.innerWidth - 200)}px`;
    style.top = `${Math.min(clientY, window.innerHeight - this.element.offsetHeight - 8)}px`;
  }

  private add(text: string, action: (() => Promise<Response>) | null, title = false): void {
    const item = document.createElement("div");
    item.textContent = text;
    item.style.cssText = title
      ? "padding:4px 12px 6px;font-weight:700;border-bottom:1px solid #374151;margin-bottom:2px"
      : `padding:4px 12px;${action ? "cursor:pointer" : "color:#9ca3af"}`;
    if (action) {
      item.addEventListener("mouseenter", () => (item.style.background = "#1f2937"));
      item.addEventListener("mouseleave", () => (item.style.background = ""));
      item.addEventListener("click", () => {
        void action();
        this.close();
      });
    }
    this.element.appendChild(item);
  }
}
