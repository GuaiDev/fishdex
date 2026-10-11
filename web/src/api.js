// The built app is always served by the same backend it should talk to —
// `make serve` serves both the API and /app on whatever host:port it's
// running on (see the /app static mount in src/api/main.py). So the built
// bundle just uses its own origin, no environment branching needed.
// Only the Vite dev server (npm run dev, a separate process with no
// backend of its own) needs an explicit override — pointed at the local
// backend started by `make serve`.
const BASE_URL = import.meta.env.DEV ? 'http://localhost:8000' : window.location.origin;

// catches[].photo_url from GET /sessions is a relative path (e.g. /photos/x.jpg).
// In production that resolves fine since the backend serves the app itself,
// but resolve it explicitly so it also works wherever frontend and backend
// are on different origins (e.g. local dev).
export function resolvePhotoUrl(path) {
  if (!path) return null;
  return path.startsWith('http') ? path : `${BASE_URL}${path}`;
}

function getToken() {
  return localStorage.getItem('fishbot_token');
}

function authHeaders() {
  const token = getToken();
  const h = { 'Content-Type': 'application/json' };
  if (token) h['Authorization'] = `Bearer ${token}`;
  return h;
}

export async function signup(code, username, displayName) {
  const res = await fetch(`${BASE_URL}/auth/signup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, username, display_name: displayName }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `Signup failed: ${res.status}`);
  return data;
}

export async function getMe() {
  const res = await fetch(`${BASE_URL}/auth/me`, { headers: authHeaders() });
  if (res.status === 401) return null;
  if (!res.ok) throw new Error(`Auth check failed: ${res.status}`);
  return res.json();
}

export async function sendMessage(message, conversationHistory = []) {
  const res = await fetch(`${BASE_URL}/chat`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ message, conversation_history: conversationHistory }),
  });
  if (res.status === 429) {
    const data = await res.json();
    throw Object.assign(new Error(data.detail || 'Rate limit reached'), { status: 429 });
  }
  if (res.status === 401) throw Object.assign(new Error('Not authenticated'), { status: 401 });
  if (!res.ok) throw new Error(`Chat failed: ${res.status}`);
  return res.json();
}

// TODO(backend): the backend accepts `photos[]` (one extra file per
// catches_json entry, by index — see log_trip_with_photo in src/api/main.py)
// but this function still only ever sends a single `photo` file. Wire up
// per-catch photo uploads here to close that gap; count/size/bait already
// persist correctly via catches_json (see trip_logger.py's structured_catches
// path).
// startedAt/endedAt: the "Start fishing" timer's two ends as ISO strings
// (toISOString, so always with a timezone), or null for a plain log.
export async function logTrip(text, photoLat, photoLng, photoTakenAt, photoFile, catches, startedAt = null, endedAt = null) {
  let res;
  if (photoFile) {
    const form = new FormData();
    form.append('text', text);
    form.append('photo', photoFile);
    if (photoLat) { form.append('photo_lat', photoLat); form.append('photo_lng', photoLng); }
    if (photoTakenAt) form.append('photo_taken_at', photoTakenAt);
    if (catches) form.append('catches_json', JSON.stringify(catches));
    if (startedAt) form.append('started_at', startedAt);
    if (endedAt) form.append('ended_at', endedAt);

    const token = getToken();
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    res = await fetch(`${BASE_URL}/log-trip/photo`, { method: 'POST', headers, body: form });
  } else {
    const body = { text };
    if (photoLat) { body.photo_lat = photoLat; body.photo_lng = photoLng; }
    if (photoTakenAt) body.photo_taken_at = photoTakenAt;
    if (catches) body.catches_json = JSON.stringify(catches);
    if (startedAt) body.started_at = startedAt;
    if (endedAt) body.ended_at = endedAt;

    res = await fetch(`${BASE_URL}/log-trip`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(body),
    });
  }
  if (res.status === 401) throw Object.assign(new Error('Not authenticated'), { status: 401 });
  if (!res.ok) throw new Error(`Log trip failed: ${res.status}`);
  return res.json();
}

export async function confirmCatchSpecies(catchId, species) {
  const res = await fetch(`${BASE_URL}/catches/${catchId}/confirm-species`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ species }),
  });
  if (res.status === 401) throw Object.assign(new Error('Not authenticated'), { status: 401 });
  if (!res.ok) throw new Error(`Confirm species failed: ${res.status}`);
  return res.json();
}

export async function getFishDex() {
  const res = await fetch(`${BASE_URL}/fishdex`, { headers: authHeaders() });
  if (res.status === 401) throw Object.assign(new Error('Not authenticated'), { status: 401 });
  if (!res.ok) throw new Error(`FishDex fetch failed: ${res.status}`);
  return res.json();
}

export async function getSessions() {
  const res = await fetch(`${BASE_URL}/sessions`, { headers: authHeaders() });
  if (res.status === 401) throw Object.assign(new Error('Not authenticated'), { status: 401 });
  if (!res.ok) throw new Error(`Sessions failed: ${res.status}`);
  return res.json();
}

// Live conditions for the Chat coach's evidence panel. Reads through the same
// weather functions the coach's own tools use, so the panel shows the data the
// coach reasons from. lat/lng are optional — pass them ONLY when the browser
// already has a granted geolocation permission; otherwise the backend resolves
// location from the user's last trip, then a regional default, so the response
// is never empty. Callers should degrade quietly (hide the panel) on failure,
// never surface a hard error here — the Chat screen must stay calm.
export async function getConditions(lat, lng) {
  const qs = (lat != null && lng != null)
    ? `?${new URLSearchParams({ lat, lng })}`
    : '';
  const res = await fetch(`${BASE_URL}/conditions${qs}`, { headers: authHeaders() });
  if (res.status === 401) throw Object.assign(new Error('Not authenticated'), { status: 401 });
  if (!res.ok) throw new Error(`Conditions fetch failed: ${res.status}`);
  return res.json();
}

export async function getMapSegments(bounds, mode = 'balanced') {
  const { north, south, east, west } = bounds;
  const params = new URLSearchParams({ north, south, east, west, mode });
  const res = await fetch(`${BASE_URL}/map/segments?${params}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error('Failed to fetch segments');
  return res.json();
}

// ── Personal-map pins ──────────────────────────────────────────────────────
// One JSON helper for every pin and trip-edit route. A 404 means "not yours
// or not there" — the server never says which.
async function pinRequest(method, path, body) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: authHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) throw Object.assign(new Error('Not authenticated'), { status: 401 });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : `Request failed: ${res.status}`;
    throw Object.assign(new Error(detail), { status: res.status });
  }
  return data;
}

export const getPinLabels = () => pinRequest('GET', '/pins/labels');
// { pins: [...], unpinned_stops: [...] } — the Personal map.
export const getPins = () => pinRequest('GET', '/pins');
export const getPin = id => pinRequest('GET', `/pins/${id}`);
// fields: { name, lat, lng, notes?, is_favourite?, from_stop_id? }
export const createPin = fields => pinRequest('POST', '/pins', fields);
// fields: any of { name, notes, is_favourite }; notes: null clears them.
export const updatePin = (id, fields) => pinRequest('PATCH', `/pins/${id}`, fields);
export const deletePin = id => pinRequest('DELETE', `/pins/${id}`);
export const mergePin = (id, intoPinId) => pinRequest('POST', `/pins/${id}/merge`, { into_pin_id: intoPinId });
export const linkStopToPin = (pinId, stopId) => pinRequest('POST', `/pins/${pinId}/links`, { stop_id: stopId });
export const unlinkStopFromPin = (pinId, stopId) => pinRequest('DELETE', `/pins/${pinId}/links/${stopId}`);
// fields: { date_approx?, species: [], rating? }
export const addRecalledVisit = (pinId, fields) => pinRequest('POST', `/pins/${pinId}/recalled`, fields);
export const deleteRecalledVisit = (pinId, id) => pinRequest('DELETE', `/pins/${pinId}/recalled/${id}`);
// fields: any of { rating, started_at, ended_at }; null clears.
export const updateSession = (id, fields) => pinRequest('PATCH', `/sessions/${id}`, fields);
export const setTripPinRating = (sessionId, pinId, rating) =>
  pinRequest('PUT', `/sessions/${sessionId}/pins/${pinId}/rating`, { rating });
