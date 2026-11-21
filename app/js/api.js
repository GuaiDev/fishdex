// The one place that talks to the FishDex API. Everything else calls these.

const KEY = 'fishdex.conn.v1';
// config.js (written by scripts/build_vercel.sh on the hosted site) sets the default API address.
export const DEFAULT_BASE = (window.FISHDEX_CONFIG && window.FISHDEX_CONFIG.api) || 'http://localhost:8000';

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
// No token means the public read-only demo: the map and field guide work, nothing is saved or sent to the coach.
export const isDemo = () => !conn.token;

export function saveConn(next) {
  const base = (next.base || DEFAULT_BASE).replace(/\/+$/, '');
  let token = cleanToken(next.token);
  // A token belongs to the server that issued it. Never carry it to a different address.
  if (base !== conn.base && token === conn.token) token = '';
  conn = { base, token };
  try { localStorage.setItem(KEY, JSON.stringify(conn)); } catch (e) { /* see store.js */ }
}

// ?api= may only point at the server this build was configured for, or at a local one for development.
export function isAllowedBase(value) {
  let u;
  try { u = new URL(value); } catch (e) { return false; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
  if (u.hostname === 'localhost' || u.hostname === '127.0.0.1' || u.hostname === '[::1]') return true;
  try { return u.origin === new URL(DEFAULT_BASE).origin; } catch (e) { return false; }
}

// Reads ?api=, ?token= and #token= from the link, removes them from the address bar, and
// returns the token to confirm (or ''). A token from a link is never saved here: the caller
// shows who it signs in as and saves only if the person agrees.
export function takeLinkParams(loc = location, hist = history) {
  const q = new URLSearchParams(loc.search);
  const f = new URLSearchParams((loc.hash || '').replace(/^#/, ''));
  if (!q.has('api') && !q.has('token') && !f.has('token')) return { token: '' };
  const wanted = q.get('api');
  if (wanted && isAllowedBase(wanted)) saveConn({ base: wanted, token: conn.token });
  const token = cleanToken(f.get('token') || q.get('token') || '');
  const hash = f.has('token') ? '' : loc.hash;
  hist.replaceState(null, '', loc.pathname + hash);
  return { token };
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
      detail = typeof j.detail === 'string' ? j.detail : (j.detail && j.detail.message) || JSON.stringify(j.detail || j);
    } catch (e) { /* not JSON */ }
    // A sign-in attempt passes its own token and handles its own 401.
    if (res.status === 401 && token === undefined) authListeners.forEach((fn) => fn(detail));
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export const me = (override) => request('/auth/me', override);

export const onboardingQuestions = (base) => request('/onboarding/questions', { base, token: '' });

// An account is made from an invite code, a username and an answer to every onboarding question.
export const signup = (base, code, username, answers) =>
  request('/auth/signup', { method: 'POST', base, token: '', body: { code, username, answers } });

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
