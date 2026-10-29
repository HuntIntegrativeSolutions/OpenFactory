// fetch() for the instructor endpoints, /api/fault and POST /api/exam (PLAN §6.4). On a server
// bound beyond loopback they need a token: on a 401 this asks for it once, keeps it for the
// browser tab (sessionStorage), and sends it in the X-OpenFactory-Fault-Token header.

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
    // storage blocked: the token lives only in this page
  }
}

let token: string | null = null;
let loaded = false;

/** fetch() with the token, asking for it once if the server answers 401. */
export async function guardedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  if (!loaded) {
    token = storedToken();
    loaded = true;
  }
  const send = () => {
    const headers = new Headers(init.headers);
    if (token) headers.set(TOKEN_HEADER, token);
    return fetch(url, { ...init, headers });
  };
  let res = await send();
  if (res.status === 401) {
    const entered = window.prompt("This server needs its instructor token:");
    token = entered ? entered.trim() : null;
    storeToken(token);
    if (token) res = await send();
  }
  return res;
}

/** Forget a token the server rejected, so the next call asks again. */
export function forgetToken(): void {
  token = null;
  storeToken(null);
}
