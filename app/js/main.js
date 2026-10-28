// The shell: one header, one set of screens kept alive, one shared store, one sign-in.

import * as api from './api.js';
import * as store from './store.js';
import { h, clear } from './util.js';
import { initMap } from './map.js';
import { initChat } from './chat.js';
import { initGuide } from './guide.js';

const SCREENS = [
  { id: 'map', label: 'Map', icon: 'M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14' },
  { id: 'coach', label: 'Coach', icon: 'M4 5h16v11H9l-5 4zM8 9h8M8 12h5' },
  { id: 'guide', label: 'Field guide', icon: 'M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5zM19 19v2H6' },
  { id: 'anglers', label: 'Anglers', icon: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c0-4 3.6-7 8-7s8 3 8 7', sample: '../design/lavish/fishdex-anglers.html' },
  { id: 'theme', label: 'Theme', icon: 'M12 3a9 9 0 1 0 0 18c1.7 0 2-1.2 1.3-2.2-.8-1.2-.1-2.8 1.4-2.8H17a4 4 0 0 0 4-4c0-5-4-9-9-9z', sample: '../design/lavish/theme-rebuild.html' },
];

const $ = (id) => document.getElementById(id);
const panes = {};

// ?api=http://localhost:8000&token=... in the link saves the connection and is then removed.
function takeLinkParams() {
  const q = new URLSearchParams(location.search);
  if (!q.has('token') && !q.has('api')) return;
  const cur = api.getConn();
  api.saveConn({ base: q.get('api') || cur.base, token: q.get('token') || cur.token });
  history.replaceState(null, '', location.pathname + location.hash);
}

function buildNav() {
  const seg = $('seg'), tabs = $('tabs'), stage = $('stage');
  for (const s of SCREENS) {
    const link = (cls) => h('a', { href: `#/${s.id}`, 'data-id': s.id, class: cls }, s.label);
    seg.append(link());
    tabs.append(h('a', { href: `#/${s.id}`, 'data-id': s.id },
      (() => { const el = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        el.setAttribute('viewBox', '0 0 24 24'); el.setAttribute('aria-hidden', 'true');
        const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', s.icon);
        el.append(p); return el; })(), s.label));
    panes[s.id] = h('section', { class: 'pane', id: `pane-${s.id}`, 'aria-label': s.label });
    stage.append(panes[s.id]);
  }
}

function route() {
  const id = (location.hash.replace(/^#\/?/, '').split('/')[0]) || 'map';
  const s = SCREENS.find((x) => x.id === id) || SCREENS[0];
  for (const x of SCREENS) panes[x.id].classList.toggle('on', x.id === s.id);
  for (const c of [$('seg'), $('tabs')]) {
    for (const a of c.children) {
      if (a.dataset.id === s.id) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    }
  }
  if (s.sample && !panes[s.id].firstChild) {
    panes[s.id].append(h('p', { class: 'sample-note' }, 'This screen is still the sample mockup. It connects to the API later.'),
      h('iframe', { class: 'screen-frame', title: s.label, src: s.sample }));
  }
  document.title = `FishDex — ${s.label}`;
  window.dispatchEvent(new CustomEvent('fishdex:screen', { detail: s.id }));
}

const goto = (id) => { location.hash = `#/${id}`; };

function drawPlaceChip() {
  const chip = $('place'), pl = store.get().place;
  clear(chip);
  chip.hidden = !pl;
  if (!pl) return;
  chip.append(h('span', { class: 'pin' }, '◉'), h('span', { class: 'name' }, pl.name),
    h('button', { type: 'button', 'aria-label': 'Clear place', onclick: () => store.clearPlace() }, '×'));
}

// ── connection / sign-in ──────────────────────────────────────────────────
function showConnect(message) {
  const dlg = $('connect');
  const conn = api.getConn();
  const err = h('p', { class: 'form-err', role: 'alert' }, message || '');
  const base = h('input', { value: conn.base, 'aria-label': 'API address', autocomplete: 'off' });
  const token = h('input', { placeholder: 'Paste the token', 'aria-label': 'Token', autocomplete: 'off', type: 'password' });
  const code = h('input', { placeholder: 'Invite code', 'aria-label': 'Invite code', autocomplete: 'off' });
  const user = h('input', { placeholder: 'Pick a username', 'aria-label': 'Username', autocomplete: 'off' });

  async function finish(newConn) {
    try {
      const u = await api.me({ base: newConn.base.replace(/\/+$/, ''), token: api.cleanToken(newConn.token) });
      api.saveConn(newConn);
      dlg.close();
      setUser(u);
      start();
    } catch (e) { err.textContent = e.status === 401 ? 'That token was not accepted.' : e.message; }
  }
  clear(dlg).append(h('form', { method: 'dialog', class: 'connect-form', onsubmit: (e) => e.preventDefault() },
    h('p', { class: 'kicker' }, 'FishDex'), h('h2', null, 'Connect to your FishDex'),
    h('p', { class: 'muted' }, 'This app talks to your own FishDex server. Start it with ', h('code', null, 'make serve'), ', then:'),
    h('label', null, 'API address', base),
    h('label', null, h('span', null, 'Token ', h('span', { class: 'muted small' }, '(run ', h('code', null, 'uv run python -m src.cli.main token'), ')')), token),
    h('button', { class: 'btn primary', type: 'button', onclick: () => finish({ base: base.value, token: token.value }) }, 'Connect'),
    h('details', null, h('summary', null, 'I have an invite code'),
      h('label', null, 'Invite code', code), h('label', null, 'Username', user),
      h('button', { class: 'btn', type: 'button', onclick: async () => {
        try {
          const r = await api.signup(base.value.replace(/\/+$/, ''), code.value.trim(), user.value.trim());
          finish({ base: base.value, token: r.token });
        } catch (e) { err.textContent = e.message; }
      } }, 'Sign up')),
    err));
  if (!dlg.open) dlg.showModal();
}

function setUser(u) {
  const el = $('who');
  clear(el);
  el.append(h('button', { type: 'button', title: 'Connection settings', onclick: () => showConnect() },
    h('i', { class: 'live' }), u ? (u.display_name || u.username) : 'Not connected'));
}

let started = false;
let mapApi = null;
async function start() {
  if (!started) {
    started = true;
    mapApi = initMap(panes.map, { goto });
    initChat(panes.coach);
    initGuide(panes.guide, { goto });
    route();
  }
  mapApi.reboot();
}

async function init() {
  takeLinkParams();
  buildNav();
  store.subscribe(drawPlaceChip);
  drawPlaceChip();
  window.addEventListener('hashchange', route);
  api.onAuthNeeded((msg) => showConnect(msg || 'Please sign in again.'));
  setUser(null);
  if (!api.getConn().token) { route(); showConnect(); return; }
  try {
    setUser(await api.me());
    start();
  } catch (e) {
    route();
    if (e.status !== 401) showConnect(e.message);
  }
}

init();
