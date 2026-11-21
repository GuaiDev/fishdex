// First-run questions. Every account answers all of them before it exists: there is
// no skip, and the submit button stays disabled until each question has an answer.

import { h } from './util.js';

const HOME_FIELDS = [
  ['name', 'Place name', 'text'],
  ['lat', 'Latitude', 'number'],
  ['lng', 'Longitude', 'number'],
  ['jurisdiction', 'Jurisdiction code (e.g. CA-ON)', 'text'],
];

// Returns { el, answers(), complete() } for a question list from GET /onboarding/questions.
// `onChange` fires whenever an answer changes so the caller can enable its submit button.
export function renderQuestions(questions, onChange) {
  const state = {};
  const fire = () => onChange && onChange();

  const blocks = questions.map((q) => {
    const body = h('div', { class: 'q-body' });
    if (q.kind === 'choice' || q.kind === 'multi_choice') {
      const multi = q.kind === 'multi_choice';
      for (const o of q.options) {
        const input = h('input', { type: multi ? 'checkbox' : 'radio', name: `q-${q.id}`, value: o.value });
        input.addEventListener('change', () => {
          if (multi) {
            const set = new Set(state[q.id] || []);
            if (input.checked) set.add(o.value); else set.delete(o.value);
            state[q.id] = [...set];
          } else {
            state[q.id] = o.value;
          }
          fire();
        });
        body.append(h('label', { class: 'q-opt' }, input, ' ', o.label));
      }
    } else if (q.kind === 'home') {
      const home = {};
      for (const [field, label, type] of HOME_FIELDS) {
        const input = h('input', { type, step: type === 'number' ? 'any' : undefined, 'aria-label': label, placeholder: label, autocomplete: 'off' });
        input.addEventListener('input', () => {
          const v = input.value.trim();
          if (v === '') delete home[field]; else home[field] = type === 'number' ? Number(v) : v;
          if (HOME_FIELDS.every(([f]) => home[f] !== undefined && home[f] === home[f])) state[q.id] = { ...home };
          else delete state[q.id];
          fire();
        });
        body.append(input);
      }
    }
    return h('fieldset', { class: 'q' }, h('legend', null, q.prompt), body);
  });

  const el = h('div', { class: 'questions' }, ...blocks);
  return {
    el,
    answers: () => ({ ...state }),
    complete: () => questions.every((q) => state[q.id] !== undefined && state[q.id].length !== 0),
  };
}
