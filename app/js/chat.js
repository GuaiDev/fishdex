// The coach: a chat screen on the real /chat endpoint (src/agent). The shared place
// from the map rides along as one sentence in front of the question, so "what's it
// like here?" means the spot the angler is looking at.

import * as api from './api.js';
import * as store from './store.js';
import { h, clear, renderMarkdown } from './util.js';

export function initChat(root) {
  const log = h('div', { class: 'chat-log', role: 'log', 'aria-live': 'polite' });
  const ctx = h('div', { class: 'chat-ctx' });
  const sugg = h('div', { class: 'chat-sugg' });
  const input = h('textarea', {
    class: 'chat-input', rows: '1', placeholder: 'Ask about a river, a species, a plan…',
    'aria-label': 'Message to the coach',
  });
  const send = h('button', { class: 'btn primary', type: 'submit' }, 'Send');
  const usage = h('span', { class: 'muted small' });
  const form = h('form', { class: 'chat-form' }, input, send);
  root.append(
    h('div', { class: 'chat-head' },
      h('div', null, h('p', { class: 'kicker' }, 'Coach'), h('h2', null, 'Ask the fishing coach')),
      h('button', { class: 'btn', type: 'button', onclick: () => { store.newChat(); draw(); } }, 'New chat')),
    log, h('div', { class: 'chat-foot' }, ctx, sugg, form, usage));

  let includePlace = true;
  let busy = false;

  const turns = () => store.get().chat.turns;

  function bubble(t) {
    const cls = `msg ${t.role}${t.error ? ' error' : ''}`;
    if (t.role === 'user') return h('div', { class: cls }, h('p', null, t.content));
    const meta = [];
    if (t.tools && t.tools.length) meta.push(h('div', { class: 'tools' }, 'Looked up: ',
      [...new Set(t.tools)].map((n) => h('span', { class: 'tag' }, n.replace(/_/g, ' ')))));
    return h('div', { class: cls }, t.error ? h('p', null, t.content) : renderMarkdown(t.content), meta);
  }

  function draw() {
    clear(log);
    const ts = turns();
    if (!ts.length) {
      log.append(h('div', { class: 'chat-empty' },
        h('h3', null, store.get().place ? `Ask about ${store.get().place.name}` : 'Start with a place'),
        h('p', { class: 'muted' }, store.get().place
          ? 'The coach knows which water you picked on the map. Ask, or try one of the questions below.'
          : 'Pick a spot or river on the map and the coach will know where you mean. Or just ask.')));
    }
    ts.forEach((t) => log.append(bubble(t)));
    if (busy) log.append(h('div', { class: 'msg assistant typing' }, h('span'), h('span'), h('span')));
    log.scrollTop = log.scrollHeight;
    drawContext();
  }

  function drawContext() {
    const place = store.get().place;
    clear(ctx);
    clear(sugg);
    if (place) {
      ctx.append(h('label', { class: 'ctx-pill' },
        h('input', { type: 'checkbox', checked: includePlace || null, onchange: (e) => { includePlace = e.target.checked; } }),
        h('span', null, 'About ', h('b', null, place.name))));
    } else {
      ctx.append(h('span', { class: 'muted small' }, 'No place picked. Choose one on the map to give the coach context.'));
    }
    if (!turns().length) {
      const qs = place
        ? [`What should I know about fishing ${place.name}?`, 'How do I get there?', 'What might I catch here?']
        : ['Where could I fish near me?', 'What are some good microfishing targets?'];
      qs.forEach((q) => sugg.append(h('button', { class: 'chip', type: 'button', onclick: () => ask(q) }, q)));
    }
  }

  async function refreshUsage() {
    try {
      const u = await api.me();
      usage.textContent = `${u.usage_today} of ${u.daily_limit} messages used today`;
    } catch (e) { usage.textContent = ''; }
  }

  async function ask(text) {
    text = (text || '').trim();
    if (!text || busy) return;
    const place = store.get().place;
    const sent = includePlace && place
      ? `[The angler is looking at: ${store.placeSentence(place)}.]\n${text}` : text;
    const chat = store.get().chat;
    const history = chat.turns.filter((t) => !t.error).map((t) => ({ role: t.role, content: t.sent || t.content }));
    const session = chat.session || `web-${Date.now()}`;
    store.saveChat({ session, turns: [...chat.turns, { role: 'user', content: text, sent }] });
    input.value = '';
    busy = true; send.disabled = true; draw();
    try {
      const r = await api.chat(sent, history, session);
      // With no model key the server answers 200 with its own message and never adds an
      // assistant turn to `messages`. A real answer always ends the history; show the
      // other as the problem it is.
      const last = r.messages && r.messages[r.messages.length - 1];
      const failed = !last || last.role !== 'assistant';
      store.saveChat({ session, turns: [...turns(), {
        role: 'assistant', content: r.reply, tools: r.tool_calls || [], mode: r.mode, error: failed || null,
      }] });
    } catch (e) {
      store.saveChat({ session, turns: [...turns(), { role: 'assistant', content: e.message, error: true }] });
    }
    busy = false; send.disabled = false; draw(); refreshUsage();
    input.focus();
  }

  form.addEventListener('submit', (e) => { e.preventDefault(); ask(input.value); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(input.value); }
  });
  input.addEventListener('input', () => {
    input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  });

  // A pick on the map changes the context line live.
  store.subscribe(() => { if (!busy) drawContext(); });
  window.addEventListener('fishdex:screen', (e) => {
    if (e.detail === 'coach') { draw(); refreshUsage(); setTimeout(() => input.focus(), 0); }
  });
  draw();
  return { ask, refreshUsage };
}
