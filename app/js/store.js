// Shared state for every screen. One place the angler is "looking at" (a spot or a
// stretch of water) and one conversation with the coach. Screens read it and
// subscribe; they never keep their own copy of the selection.

const KEY = 'fishdex.state.v1';
const listeners = new Set();

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '{}');
    return { place: raw.place || null, chat: raw.chat || { session: null, turns: [] } };
  } catch (e) {
    return { place: null, chat: { session: null, turns: [] } };
  }
}

let state = load();

function persist() {
  try {
    localStorage.setItem(KEY, JSON.stringify({ place: state.place, chat: state.chat }));
  } catch (e) { /* private window or full storage: the app works without it */ }
}

export const get = () => state;

export function set(patch) {
  state = { ...state, ...patch };
  persist();
  listeners.forEach((fn) => fn(state));
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// A place is {kind: 'spot'|'water', id, name, subtitle, lat, lng, jurisdiction, spotKind, waterBodyId}.
export function selectPlace(place) {
  set({ place });
}

export function clearPlace() {
  set({ place: null });
}

export function newChat() {
  set({ chat: { session: null, turns: [] } });
}

export function saveChat(chat) {
  set({ chat });
}

// One sentence the coach can use to know which water the angler means. The coach has
// its own place tools; this only says where to look.
export function placeSentence(place) {
  if (!place) return '';
  const where = place.jurisdiction ? ` in ${place.jurisdiction}` : '';
  const at = place.lat != null ? ` (around ${place.lat.toFixed(4)}, ${place.lng.toFixed(4)})` : '';
  if (place.kind === 'spot') {
    const kind = place.spotKind ? ` — a ${place.spotKind.replace(/_/g, ' ')} spot` : '';
    return `${place.name}${kind}${at}${where}`;
  }
  return `${place.name}${at}${where}`;
}
