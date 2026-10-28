// Field guide screen. The guide itself is the unchanged Lavish mockup (sample species,
// connected later). Above it sits a live strip: the species the records hold near the
// place picked on the map, straight from the spot card.

import * as api from './api.js';
import * as store from './store.js';
import { h, clear } from './util.js';

export function initGuide(root, { goto }) {
  const strip = h('section', { class: 'guide-strip', 'aria-live': 'polite' });
  const frame = h('iframe', { class: 'screen-frame', title: 'Field guide (sample species)', loading: 'lazy' });
  frame.src = '../design/lavish/fishdex-field-guide.html';
  root.append(strip, h('p', { class: 'sample-note' }, 'The guide below is still sample data; the strip above is live.'), frame);

  let drawnFor = null;

  async function draw() {
    const place = store.get().place;
    const key = place ? place.id : null;
    if (key === drawnFor && strip.dataset.ready) return;
    drawnFor = key;
    strip.dataset.ready = '1';
    clear(strip);
    if (!place) {
      strip.append(h('div', null, h('p', { class: 'kicker' }, 'Near you'),
        h('p', { class: 'muted' }, 'Pick a spot on the map and the species on record around it show here.')),
        h('button', { class: 'btn', type: 'button', onclick: () => goto('map') }, 'Open the map'));
      return;
    }
    strip.append(h('div', null, h('p', { class: 'kicker' }, 'Near'), h('h3', null, place.name),
      h('p', { class: 'muted small' }, 'Loading species on record…')));
    if (place.kind !== 'spot') {
      clear(strip).append(h('div', null, h('p', { class: 'kicker' }, 'Near'), h('h3', null, place.name),
        h('p', { class: 'muted' }, 'Species records are looked up per spot. Pick a spot on this water to see them.')),
        h('button', { class: 'btn', type: 'button', onclick: () => goto('map') }, 'Pick a spot'));
      return;
    }
    let d;
    try {
      d = await api.mapSpot(place.id);
    } catch (e) {
      if (drawnFor === key) clear(strip).append(h('p', { class: 'muted' }, e.message));
      return;
    }
    if (drawnFor !== key) return;
    const mf = d.card.might_find;
    clear(strip).append(
      h('div', { class: 'strip-main' },
        h('p', { class: 'kicker' }, 'Species on record near'), h('h3', null, place.name),
        mf.species.length
          ? h('div', { class: 'chips wrap' }, mf.species.map((s) => h('span', { class: 'chip static', title: `${s.cite}` },
            s.name, h('span', { class: 'tag' }, s.evidence))))
          : h('p', { class: 'muted' }, mf.empty_text || 'No species on record here.'),
        h('p', { class: 'caveat' }, mf.caveat)),
      h('div', { class: 'actions' },
        h('button', { class: 'btn primary', type: 'button', onclick: () => goto('coach') }, 'Ask the coach'),
        h('button', { class: 'btn', type: 'button', onclick: () => goto('map') }, 'Show on map')));
  }

  store.subscribe(() => draw());
  window.addEventListener('fishdex:screen', (e) => { if (e.detail === 'guide') { strip.dataset.ready = ''; draw(); } });
  draw();
}
