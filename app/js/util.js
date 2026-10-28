// Small helpers shared by the screens.

// h('div', {class: 'x', onclick: fn}, 'text', child) — builds DOM, never parses HTML,
// so text from the API can't inject markup.
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

// Replace an element's children; skips null/false and flattens arrays like h() does.
export const fill = (el, ...kids) => {
  clear(el);
  el.append(...kids.flat(Infinity).filter((k) => k != null && k !== false));
  return el;
};

export const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

const KIND_COLORS = {
  river_mouth: '#2E6B60', creek_junction: '#6B7A3A', below_dam: '#A8543A',
  public_fishing: '#B08A3E', quiet_water: '#5E7A86',
};
// A kind the API adds later still gets a colour, just not a designed one.
export const kindColor = (k) => KIND_COLORS[k] || '#7A6A8A';

// The coach writes light markdown (paragraphs, lists, bold). Escape first, then
// turn a small safe subset into elements.
export function renderMarkdown(text) {
  const root = h('div', { class: 'md' });
  const inline = (s) => {
    const frag = document.createDocumentFragment();
    const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;
    let last = 0, m;
    while ((m = re.exec(s))) {
      if (m.index > last) frag.append(s.slice(last, m.index));
      const t = m[0];
      if (t.startsWith('**')) frag.append(h('strong', null, t.slice(2, -2)));
      else if (t.startsWith('`')) frag.append(h('code', null, t.slice(1, -1)));
      else frag.append(h('em', null, t.slice(1, -1)));
      last = m.index + t.length;
    }
    if (last < s.length) frag.append(s.slice(last));
    return frag;
  };
  let list = null;
  const flush = () => { list = null; };
  for (const raw of String(text).split('\n')) {
    const line = raw.trimEnd();
    const bullet = /^\s*[-*•]\s+(.*)/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)/.exec(line);
    const heading = /^#{1,4}\s+(.*)/.exec(line);
    if (bullet || numbered) {
      const tag = bullet ? 'ul' : 'ol';
      if (!list || list.tagName.toLowerCase() !== tag) { list = h(tag); root.append(list); }
      list.append(h('li', null, inline((bullet || numbered)[1])));
    } else if (heading) {
      flush(); root.append(h('h4', null, inline(heading[1])));
    } else if (!line.trim()) {
      flush();
    } else {
      flush(); root.append(h('p', null, inline(line)));
    }
  }
  return root;
}

export const titleCase = (s) => String(s).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

export const debounce = (fn, ms) => {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
};
