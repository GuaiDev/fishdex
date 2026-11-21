// Run with: node --test tests/js
// The first-run form is complete only when every question has an answer; there is no skip.
import test from 'node:test';
import assert from 'node:assert/strict';

class El {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.on = {}; this.value = ''; this.checked = false; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(t, fn) { this.on[t] = fn; }
  append(...k) { this.kids.push(...k); }
  get nodeType() { return 1; }
  all(pred, out = []) { if (pred(this)) out.push(this); this.kids.forEach((k) => k.all && k.all(pred, out)); return out; }
  fire(t) { this.on[t] && this.on[t](); }
}
globalThis.document = { createElement: (t) => new El(t), createTextNode: (s) => ({ nodeType: 3, text: s }) };
const { renderQuestions } = await import('../../app/js/onboard.js');

const QUESTIONS = [
  { id: 'home', kind: 'home', prompt: 'Home?', options: [] },
  { id: 'water', kind: 'choice', prompt: 'Water?', options: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }] },
  { id: 'fish', kind: 'multi_choice', prompt: 'Fish?', options: [{ value: 'x', label: 'X' }, { value: 'y', label: 'Y' }] },
];
const inputs = (form) => form.el.all((e) => e.tag === 'input');

function answerHome(form) {
  const [name, lat, lng, jur] = inputs(form).filter((i) => i.attrs.type === 'text' || i.attrs.type === 'number');
  for (const [i, v] of [[name, 'Oakville'], [lat, '43.4'], [lng, '-79.6'], [jur, 'CA-ON']]) { i.value = v; i.fire('input'); }
}

test('no question answered: not complete', () => {
  assert.equal(renderQuestions(QUESTIONS).complete(), false);
});

test('one question left unanswered keeps the form incomplete', () => {
  const form = renderQuestions(QUESTIONS);
  answerHome(form);
  const radio = inputs(form).find((i) => i.attrs.type === 'radio');
  radio.checked = true; radio.fire('change');
  assert.equal(form.complete(), false);
});

test('every question answered: complete, with the answers in the shape the API expects', () => {
  let changes = 0;
  const form = renderQuestions(QUESTIONS, () => { changes += 1; });
  answerHome(form);
  const radio = inputs(form).find((i) => i.attrs.type === 'radio');
  radio.checked = true; radio.fire('change');
  const boxes = inputs(form).filter((i) => i.attrs.type === 'checkbox');
  boxes[1].checked = true; boxes[1].fire('change');
  assert.equal(form.complete(), true);
  assert.deepEqual(form.answers(), {
    home: { name: 'Oakville', lat: 43.4, lng: -79.6, jurisdiction: 'CA-ON' },
    water: 'a',
    fish: ['y'],
  });
  assert.ok(changes > 0);
});

test('unticking the last option of a multi-choice makes it unanswered again', () => {
  const form = renderQuestions([QUESTIONS[2]]);
  const box = inputs(form).find((i) => i.attrs.type === 'checkbox');
  box.checked = true; box.fire('change');
  assert.equal(form.complete(), true);
  box.checked = false; box.fire('change');
  assert.equal(form.complete(), false);
});

test('clearing a home field makes home unanswered again', () => {
  const form = renderQuestions([QUESTIONS[0]]);
  answerHome(form);
  assert.equal(form.complete(), true);
  const name = inputs(form)[0];
  name.value = ''; name.fire('input');
  assert.equal(form.complete(), false);
});
