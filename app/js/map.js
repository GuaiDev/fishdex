// Explore map: real Leaflet map on OpenStreetMap tiles, drawing live /map/* data.
// Rivers and lakes come from /map/sections, spots from /map/spots, and a tap opens
// /map/spots/{id} (the card). Selecting anything writes it to the shared store, which
// is how the field guide and the coach know what the angler is looking at.

import * as api from './api.js';
import * as store from './store.js';
import { h, clear, fill, kindColor, titleCase, debounce } from './util.js';

const L = window.L;
const WATER_STYLE = {
  river: { color: '#2E6B60', weight: 3.5, opacity: 0.85 },
  creek: { color: '#5E8F7A', weight: 2.5, opacity: 0.85 },
  lake: { color: '#3E6F86', weight: 1.5, fillColor: '#8DB4C6', fillOpacity: 0.45 },
  pond: { color: '#3E6F86', weight: 1.5, fillColor: '#8DB4C6', fillOpacity: 0.45 },
  great_lake: { color: '#3E6F86', weight: 1.5, fillColor: '#8DB4C6', fillOpacity: 0.35 },
};

// The spots endpoint is cumulative by tier: zoomed out shows headline spots only.
const tierForZoom = (z) => (z < 10 ? 1 : z < 12 ? 2 : 3);

export function initMap(root, { goto }) {
  const mapEl = h('div', { class: 'map-canvas', 'aria-label': 'Explore map' });
  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Spot kinds' });
  const status = h('div', { class: 'map-status', role: 'status' });
  const panel = h('aside', { class: 'panel', 'aria-live': 'polite' });
  root.append(mapEl, chips, status, panel);

  const map = L.map(mapEl, { zoomControl: false, preferCanvas: true }).setView([45, -79], 6);
  L.control.zoom({ position: 'bottomright' }).addTo(map);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  const canvas = L.canvas({ tolerance: 8 });
  const waterLayer = L.layerGroup().addTo(map);
  const labelLayer = L.layerGroup().addTo(map);
  const spotLayer = L.layerGroup().addTo(map);
  let ring = null;

  let labels = null; // /map/spots/labels
  const activeKinds = new Set();
  let newToMe = false;
  let selectedId = null;
  let inflight = null;
  let booted = false;
  let kindLabel = (k) => titleCase(k);

  const setStatus = (msg) => { status.textContent = msg || ''; status.hidden = !msg; };

  // ── chips ───────────────────────────────────────────────────────────────
  function drawChips() {
    clear(chips);
    for (const k of labels.kinds) {
      const on = activeKinds.has(k.kind);
      chips.append(h('button', {
        class: 'chip' + (on ? ' on' : ''), type: 'button', 'aria-pressed': on,
        title: k.one_liner,
        onclick: () => { on ? activeKinds.delete(k.kind) : activeKinds.add(k.kind); drawChips(); refresh(); },
      }, h('i', { class: 'dot', style: `background:${kindColor(k.kind)}` }), k.chip));
    }
    chips.append(h('button', {
      class: 'chip' + (newToMe ? ' on' : ''), type: 'button', 'aria-pressed': newToMe,
      title: 'Only spots with a recorded species you have not caught yet (your confirmed catches).',
      onclick: () => { newToMe = !newToMe; drawChips(); refresh(); },
    }, 'New to me'));
  }

  // ── data → map ──────────────────────────────────────────────────────────
  function bboxNow() {
    const b = map.getBounds();
    return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  }

  async function refresh() {
    if (!booted) return;
    if (inflight) inflight.abort();
    inflight = new AbortController();
    const { signal } = inflight;
    const bbox = bboxNow();
    const tier = tierForZoom(map.getZoom());
    try {
      const [sections, spots] = await Promise.all([
        api.mapSections({ bbox, tier, signal }),
        api.mapSpots({ bbox, tier, kinds: [...activeKinds], newToMe, signal }),
      ]);
      drawSections(sections);
      drawSpots(spots);
      const n = spots.features.length;
      setStatus(n ? '' : (newToMe ? 'No spots here with a species new to you.' : 'No spots in this view. Zoom out or pan.'));
      if (!selectedId) renderOverview(sections, spots);
    } catch (e) {
      if (e.name === 'AbortError') return;
      setStatus(e.message);
    }
  }

  function drawSections(fc) {
    waterLayer.clearLayers();
    labelLayer.clearLayers();
    L.geoJSON(fc.features.filter((f) => f.geometry), {
      renderer: canvas,
      style: (f) => WATER_STYLE[f.properties.type] || WATER_STYLE.river,
      onEachFeature: (f, layer) => {
        layer.bindTooltip(h('span', null, f.properties.name), { sticky: true });
        layer.on('click', (ev) => { L.DomEvent.stop(ev); selectSection(f); });
      },
    }).addTo(waterLayer);
    // Lakes the data has no outline for: a name on the map, still pickable.
    for (const f of (fc.labels && fc.labels.features) || []) {
      const [lng, lat] = f.geometry.coordinates;
      const p = f.properties;
      L.marker([lat, lng], {
        icon: L.divIcon({ className: 'lake-label', html: '', iconSize: [0, 0] }),
        keyboard: false,
      }).bindTooltip(h('span', null, p.display_name || p.name), { permanent: true, direction: 'center', className: 'lake-tip' })
        .on('click', () => selectWater({
          id: p.water_body_id, name: p.display_name || p.name, subtitle: p.type_label,
          lat, lng, jurisdiction: p.jurisdiction, note: p.note,
        }))
        .addTo(labelLayer);
    }
  }

  function drawSpots(fc) {
    spotLayer.clearLayers();
    // Draw headline spots last so they sit on top of the minor ones.
    const feats = [...fc.features].sort((a, b) => b.properties.tier - a.properties.tier);
    for (const f of feats) {
      const p = f.properties;
      const [lng, lat] = f.geometry.coordinates;
      L.circleMarker([lat, lng], {
        renderer: canvas, radius: p.tier === 1 ? 9 : p.tier === 2 ? 7 : 5.5,
        color: '#fff', weight: 1.5, fillColor: kindColor(p.kind), fillOpacity: 0.95,
      }).bindTooltip(h('span', null, p.title), { direction: 'top' })
        .on('click', (ev) => { L.DomEvent.stop(ev); selectSpot(p.spot_id, f); })
        .addTo(spotLayer);
    }
  }

  function highlight(lat, lng) {
    if (ring) ring.remove();
    ring = L.circleMarker([lat, lng], {
      radius: 15, color: '#1E2A27', weight: 2, fill: false, interactive: false,
    }).addTo(map);
  }

  // ── selection ───────────────────────────────────────────────────────────
  async function selectSpot(id, feature) {
    selectedId = id;
    const p = feature && feature.properties;
    if (feature) {
      const [lng, lat] = feature.geometry.coordinates;
      highlight(lat, lng);
      map.flyTo([lat, lng], Math.max(map.getZoom(), 12), { duration: 0.6 });
    }
    renderLoading(p ? p.title : 'Loading spot…');
    let d;
    try {
      d = await api.mapSpot(id);
    } catch (e) {
      if (selectedId === id) renderError(e);
      return;
    }
    if (selectedId !== id) return; // the angler already tapped something else
    highlight(d.lat, d.lng);
    store.selectPlace({
      kind: 'spot', id: d.spot_id, name: d.title, subtitle: d.subtitle, lat: d.lat, lng: d.lng,
      jurisdiction: d.jurisdiction, spotKind: d.kind, waterBodyId: d.water_body_id,
    });
    renderSpot(d);
  }

  async function selectSection(f) {
    const p = f.properties;
    selectedId = p.section_id;
    const [lng, lat] = p.label_point || [0, 0];
    highlight(lat, lng);
    if (p.bbox) {
      map.flyToBounds([[p.bbox[1], p.bbox[0]], [p.bbox[3], p.bbox[2]]], {
        paddingTopLeft: [window.innerWidth > 760 ? 420 : 20, 60], paddingBottomRight: [20, 20], duration: 0.6,
      });
    }
    store.selectPlace({
      kind: 'water', id: p.water_body_id, name: p.name, subtitle: p.summary || p.type_label,
      lat, lng, jurisdiction: p.jurisdiction, waterBodyId: p.water_body_id,
    });
    renderLoading(p.name);
    try {
      const d = await api.mapSection(p.section_id);
      if (selectedId === p.section_id) renderSection(p, d);
    } catch (e) {
      if (selectedId === p.section_id) renderError(e);
    }
  }

  function selectWater(w) {
    selectedId = w.id;
    highlight(w.lat, w.lng);
    store.selectPlace({
      kind: 'water', id: w.id, name: w.name, subtitle: w.subtitle, lat: w.lat, lng: w.lng,
      jurisdiction: w.jurisdiction, waterBodyId: w.id,
    });
    renderWater(w);
  }

  function clearSelection() {
    selectedId = null;
    if (ring) { ring.remove(); ring = null; }
    store.clearPlace();
    refresh();
    renderOverview();
  }
  map.on('click', () => { if (selectedId || store.get().place) clearSelection(); });

  // The guide strip or the coach can change the place too: follow it.
  store.subscribe((s) => {
    const pl = s.place;
    if (!pl) { if (selectedId) { selectedId = null; if (ring) { ring.remove(); ring = null; } renderOverview(); } return; }
    if (pl.id === selectedId || !booted) return;
    if (pl.kind === 'spot') {
      map.flyTo([pl.lat, pl.lng], Math.max(map.getZoom(), 13), { duration: 0.8 });
      selectSpot(pl.id, { geometry: { coordinates: [pl.lng, pl.lat] }, properties: { title: pl.name } });
    }
  });

  // ── panel ───────────────────────────────────────────────────────────────
  const closeBtn = () => h('button', { class: 'x', type: 'button', 'aria-label': 'Close', onclick: clearSelection }, '×');

  function actions(extra) {
    return h('div', { class: 'actions' },
      h('button', { class: 'btn primary', type: 'button', onclick: () => goto('coach') }, 'Ask the coach'),
      h('button', { class: 'btn', type: 'button', onclick: () => goto('guide') }, 'Field guide'),
      extra);
  }

  function renderLoading(title) {
    fill(panel, h('div', { class: 'panel-head' }, h('h2', null, title), closeBtn()),
      h('p', { class: 'muted' }, 'Loading…'));
    panel.classList.add('open');
  }

  function renderError(e) {
    fill(panel, h('div', { class: 'panel-head' }, h('h2', null, 'Could not load this'), closeBtn()),
      h('p', { class: 'muted' }, e.message));
  }

  function renderOverview(sections, spots) {
    if (selectedId) return;
    const counts = spots ? spots.meta.kind_counts : null;
    fill(panel,
      h('p', { class: 'kicker' }, 'Explore'),
      h('h2', null, 'Where would you like to look?'),
      h('p', { class: 'muted' }, 'Tap a spot or a river for what the records say about it. Whatever you pick carries into the coach and the field guide.'),
      counts ? h('ul', { class: 'legend' }, labels.kinds.map((k) => h('li', null,
        h('i', { class: 'dot', style: `background:${kindColor(k.kind)}` }),
        h('span', null, h('b', null, k.chip), ' ', h('span', { class: 'muted' }, k.one_liner)),
        h('span', { class: 'num' }, String(counts[k.kind] ?? 0))))) : null,
      spots && spots.meta.notes && spots.meta.notes.length
        ? h('p', { class: 'note' }, spots.meta.notes.join(' ')) : null);
    panel.classList.remove('open');
  }

  function block(title, open, body) {
    return h('details', { class: 'blk', open: open || null }, h('summary', null, title), body);
  }

  function speciesRow(s) {
    return h('li', null,
      h('b', null, s.name), s.scientific_name ? h('i', { class: 'sci' }, ' ' + s.scientific_name) : null,
      h('div', { class: 'muted small' }, `${s.evidence === 'survey' ? 'Survey' : 'Sighting'} · ${s.cite}`,
        s.note ? ' · ' + s.note : ''));
  }

  function renderSpot(d) {
    const c = d.card;
    const mf = c.might_find;
    const gt = c.getting_there;
    fill(panel, 
      h('div', { class: 'panel-head' },
        h('div', null,
          h('p', { class: 'kicker', style: `color:${kindColor(d.kind)}` }, kindLabel(d.kind)),
          h('h2', null, d.title), h('p', { class: 'muted' }, d.subtitle)),
        closeBtn()),
      actions(),
      h('p', { class: 'hook' }, c.hook.text),
      block(mf.title, mf.default_open, h('div', null,
        mf.species.length ? h('ul', { class: 'species' }, mf.species.map(speciesRow))
          : h('p', { class: 'muted' }, mf.empty_text || 'No species on record here.'),
        mf.more_text ? h('p', { class: 'muted small' }, mf.more_text) : null,
        mf.note ? h('p', { class: 'muted small' }, mf.note) : null,
        h('p', { class: 'caveat' }, mf.caveat))),
      c.new_to_you ? block(`${c.new_to_you.title} · ${c.new_to_you.count}`, c.new_to_you.default_open, h('div', null,
        h('ul', { class: 'species' }, c.new_to_you.species.map(speciesRow)),
        h('p', { class: 'muted small' }, c.new_to_you.note))) : null,
      block(c.why_gather.title, c.why_gather.default_open, h('div', null,
        h('p', null, c.why_gather.what_it_is),
        h('p', null, c.why_gather.general.text,
          h('span', { class: 'tag inference' }, c.why_gather.general.label)),
        c.why_gather.facts.length ? h('p', { class: 'small b' }, c.why_gather.facts_title) : null,
        c.why_gather.facts.map((f) => h('p', null, f.text, h('span', { class: 'tag record' }, 'Record · ' + f.source))))),
      block(gt.title, gt.default_open, h('div', null,
        gt.items.length ? h('ul', null, gt.items.map((i) => h('li', null, i.text,
          i.details.length ? h('span', { class: 'muted small' }, ' · ' + i.details.join(', ')) : null)))
          : h('p', { class: 'muted' }, gt.empty_text),
        h('p', { class: 'muted small' }, gt.crown_land.text),
        h('p', { class: 'small b' }, gt.unverified_title),
        h('ul', { class: 'small' }, gt.unverified.map((u) => h('li', null, u))))),
      c.caveats.map((cv) => h('p', { class: 'caveat' }, cv.text)),
      d.more_on_this_water.length ? block(`More on ${d.water_body ? d.water_body.display_name : 'this water'}`, false,
        h('ul', { class: 'pins' }, d.more_on_this_water.map((m) => h('li', null,
          h('button', { class: 'link', type: 'button', onclick: () => {
            const f = spotFeatureById(m.spot_id);
            if (f) selectSpot(m.spot_id, f); else flyToSpot(m.spot_id);
          } }, m.title))))) : null);
    panel.classList.add('open');
  }

  const spotFeatureById = () => null; // the layer only holds what is in view; fall back to a fetch
  async function flyToSpot(id) {
    try {
      const d = await api.mapSpot(id);
      map.flyTo([d.lat, d.lng], Math.max(map.getZoom(), 13), { duration: 0.8 });
      selectSpot(id, { geometry: { coordinates: [d.lng, d.lat] }, properties: { title: d.title } });
    } catch (e) { renderError(e); }
  }

  function renderSection(p, d) {
    const pins = d.features.filter((f) => f.properties.pin_type === 'spot');
    fill(panel, 
      h('div', { class: 'panel-head' },
        h('div', null, h('p', { class: 'kicker' }, `${p.type_label}${p.region ? ' · ' + p.region : ''}`),
          h('h2', null, p.name),
          h('p', { class: 'muted' }, [p.length_km ? `${p.length_km} km` : null, p.summary].filter(Boolean).join(' · '))),
        closeBtn()),
      actions(),
      h('p', { class: 'small b' }, `${pins.length} spot${pins.length === 1 ? '' : 's'} on this stretch`),
      h('ul', { class: 'pins' }, pins.map((f) => h('li', null,
        h('i', { class: 'dot', style: `background:${kindColor(f.properties.kind)}` }),
        h('button', { class: 'link', type: 'button', onclick: () => {
          const [lng, lat] = f.geometry.coordinates;
          map.flyTo([lat, lng], Math.max(map.getZoom(), 13), { duration: 0.8 });
          selectSpot(f.properties.spot_id, f);
        } }, f.properties.title)))));
    panel.classList.add('open');
  }

  function renderWater(w) {
    fill(panel, 
      h('div', { class: 'panel-head' },
        h('div', null, h('p', { class: 'kicker' }, w.subtitle || 'Water'), h('h2', null, w.name)), closeBtn()),
      actions(),
      h('p', { class: 'muted' }, w.note || 'No outline is on file for this water, so it shows as a name on the map.'));
    panel.classList.add('open');
  }

  // ── boot ────────────────────────────────────────────────────────────────
  async function boot() {
    setStatus('Loading map…');
    try {
      labels = await api.mapLabels();
      kindLabel = (k) => (labels.kinds.find((x) => x.kind === k) || {}).chip || titleCase(k);
      drawChips();
      const all = await api.mapSections({ tier: 3 });
      const boxes = [...all.features, ...((all.labels && all.labels.features) || [])]
        .map((f) => f.properties.bbox).filter(Boolean);
      if (boxes.length) {
        const w = Math.min(...boxes.map((b) => b[0])), s = Math.min(...boxes.map((b) => b[1]));
        const e = Math.max(...boxes.map((b) => b[2])), n = Math.max(...boxes.map((b) => b[3]));
        map.fitBounds([[s, w], [n, e]], { paddingTopLeft: [window.innerWidth > 760 ? 420 : 30, 60], paddingBottomRight: [30, 30] });
      }
      booted = true;
      setStatus('');
      const pl = store.get().place;
      if (pl && pl.kind === 'spot') {
        map.setView([pl.lat, pl.lng], 13);
        selectSpot(pl.id, { geometry: { coordinates: [pl.lng, pl.lat] }, properties: { title: pl.name } });
      } else if (pl) {
        store.clearPlace(); // a stretch is not reopened after a reload; don't leave a chip with nothing behind it
      }
      await refresh();
      if (!boxes.length) setStatus('This database has no map data yet. Build it with: make build-spots');
    } catch (e) {
      setStatus(e.message);
      booted = false;
    }
  }

  map.on('moveend', debounce(refresh, 250));
  window.addEventListener('fishdex:screen', (e) => {
    if (e.detail === 'map') setTimeout(() => map.invalidateSize(), 0);
  });

  return { boot, reboot: () => { booted = false; return boot(); }, map };
}
