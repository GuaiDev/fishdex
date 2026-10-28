// The one place that talks to the FishDex API. Everything else calls these.

const KEY = 'fishdex.conn.v1';
export const DEFAULT_BASE = 'http://localhost:8000';

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

export function loadConn() {
  try {
    const c = JSON.parse(localStorage.getItem(KEY) || '{}');
    return { base: c.base || DEFAULT_BASE, token: c.token || '' };
  } catch (e) {
    return { base: DEFAULT_BASE, token: '' };
  }
}

let conn = loadConn();
export const getConn = () => conn;

export function saveConn(next) {
  conn = { base: (next.base || DEFAULT_BASE).replace(/\/+$/, ''), token: cleanToken(next.token) };
  try { localStorage.setItem(KEY, JSON.stringify(conn)); } catch (e) { /* see store.js */ }
}

export function forgetToken() {
  saveConn({ base: conn.base, token: '' });
}

// `fishbot token` prints "Bearer abc..."; people paste either form.
export function cleanToken(t) {
  return (t || '').trim().replace(/^Bearer\s+/i, '');
}

const authListeners = new Set();
export const onAuthNeeded = (fn) => authListeners.add(fn);

async function request(path, { method = 'GET', body, signal, token, base } = {}) {
  const useBase = base || conn.base;
  const useToken = token !== undefined ? token : conn.token;
  const headers = {};
  if (useToken) headers.Authorization = `Bearer ${useToken}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(useBase + path, {
      method, headers, signal, body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, `Can't reach the API at ${useBase}. Is it running? (make serve)`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail || j);
    } catch (e) { /* not JSON */ }
    // A sign-in attempt passes its own token and handles its own 401.
    if (res.status === 401 && token === undefined) authListeners.forEach((fn) => fn(detail));
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export const me = (override) => request('/auth/me', override);

export const signup = (base, code, username) =>
  request('/auth/signup', { method: 'POST', base, token: '', body: { code, username } });

export const mapLabels = () => request('/map/spots/labels');

export function mapSections({ bbox, tier = 2, signal } = {}) {
  const q = new URLSearchParams({ tier });
  if (bbox) q.set('bbox', bbox.join(','));
  return request(`/map/sections?${q}`, { signal });
}

export const mapSection = (id, tier = 3) =>
  request(`/map/sections/${encodeURIComponent(id)}?tier=${tier}`);

export function mapSpots({ bbox, tier = 2, kinds, newToMe = false, signal } = {}) {
  const q = new URLSearchParams({ tier });
  if (bbox) q.set('bbox', bbox.join(','));
  if (kinds && kinds.length) q.set('kind', kinds.join(','));
  if (newToMe) q.set('new_to_me', 'true');
  return request(`/map/spots?${q}`, { signal });
}

export const mapSpot = (id) => request(`/map/spots/${encodeURIComponent(id)}`);

export const chat = (message, history, sessionId) =>
  request('/chat', {
    method: 'POST',
    body: { message, conversation_history: history, session_id: sessionId },
  });
