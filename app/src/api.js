// Where the API lives. Empty (the default) means "the origin that served this
// page", which is what local use wants. A static host sets VITE_API_BASE at
// build time to point at a separately hosted API.
export const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/+$/, '');

// Same key the previous web app used, so an existing local sign-in carries over.
const TOKEN_KEY = 'fishbot_token';

export class AuthError extends Error {}

function readToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export const hasToken = () => !!readToken();

export function clearToken() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage unavailable (private window): nothing to clear
  }
}

// Species illustrations and catch photos come back as paths like /static/…
// or /photos/…, relative to the API, not to wherever this app is hosted.
export function resolveAssetUrl(path) {
  if (!path) return null;
  return /^https?:\/\//.test(path) ? path : `${API_BASE}${path}`;
}

async function errorDetail(res, fallback) {
  try {
    const body = await res.json();
    if (body && typeof body.detail === 'string') return body.detail;
  } catch {
    // not JSON
  }
  return fallback;
}

async function readJson(res) {
  try {
    return await res.json();
  } catch {
    throw new Error(
      'Could not reach the FishDex API (the server did not answer with data). If this app is hosted separately from the API, set VITE_API_BASE at build time.',
    );
  }
}

export async function signup(code, username, displayName) {
  const res = await fetch(`${API_BASE}/auth/signup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, username, display_name: displayName }),
  });
  if (!res.ok) throw new Error(await errorDetail(res, `Sign-up failed (${res.status})`));
  const data = await readJson(res);
  try {
    localStorage.setItem(TOKEN_KEY, data.token);
  } catch {
    throw new Error('This browser is blocking storage, so it cannot stay signed in.');
  }
  return data;
}

export async function getFishDex() {
  const res = await fetch(`${API_BASE}/fishdex`, {
    headers: { Authorization: `Bearer ${readToken() ?? ''}` },
  });
  if (res.status === 401) {
    clearToken();
    throw new AuthError('Your session has ended. Sign in again.');
  }
  if (!res.ok) throw new Error(await errorDetail(res, `Could not load your FishDex (${res.status})`));
  return readJson(res);
}
