// Run with: node --test tests/js
// takeLinkParams() must never let a shared link move or replace a signed-in connection.
import test from 'node:test';
import assert from 'node:assert/strict';

const KEY = 'fishdex.conn.v1';
const HOSTED = 'https://fishdex-api.example';
const store = new Map();
globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)) };
globalThis.window = { FISHDEX_CONFIG: { api: HOSTED } };
globalThis.location = { search: '', pathname: '/app/', hash: '' };
globalThis.history = { replaceState() {} };
const api = await import('../../app/js/api.js');

function open(link, stored = { base: HOSTED, token: 'VICTIM-TOKEN-abc123' }) {
  const u = new URL(link);
  api.saveConn({ base: 'https://reset.example', token: '' });
  api.saveConn(stored);
  const calls = [];
  const out = api.takeLinkParams({ search: u.search, pathname: u.pathname, hash: u.hash }, { replaceState: (...a) => calls.push(a) });
  return { out, conn: api.getConn(), stored: JSON.parse(store.get(KEY)), calls };
}

test('?api= to an attacker host is ignored and the token stays on the real base', () => {
  const r = open('https://fishdex-red.vercel.app/app/?api=http://attacker.example:18999');
  assert.equal(r.conn.base, HOSTED);
  assert.equal(r.conn.token, 'VICTIM-TOKEN-abc123');
  assert.deepEqual(r.stored, r.conn);
  assert.equal(r.calls[0][2], '/app/');
});

test('?api= with ?token= never saves the token and hands it back for confirmation', () => {
  const r = open('https://fishdex-red.vercel.app/app/?api=http://attacker.example:18999&token=ATTACKER-OWNED-TOKEN');
  assert.equal(r.conn.base, HOSTED);
  assert.equal(r.conn.token, 'VICTIM-TOKEN-abc123');
  assert.equal(r.out.token, 'ATTACKER-OWNED-TOKEN');
});

test('#token= is returned for confirmation, not saved, and removed from the address', () => {
  const r = open('https://fishdex-red.vercel.app/app/#token=Bearer%20LINK-TOKEN');
  assert.equal(r.out.token, 'LINK-TOKEN');
  assert.equal(r.conn.token, 'VICTIM-TOKEN-abc123');
  assert.equal(r.calls[0][2], '/app/');
});

test('?api= to localhost is allowed but drops the stored token', () => {
  const r = open('http://localhost:8080/app/?api=http://localhost:8000');
  assert.equal(r.conn.base, 'http://localhost:8000');
  assert.equal(r.conn.token, '');
});

test('?api= to the configured origin is allowed and keeps the token', () => {
  const r = open(`https://x.example/app/?api=${HOSTED}/`);
  assert.equal(r.conn.base, HOSTED);
  assert.equal(r.conn.token, 'VICTIM-TOKEN-abc123');
});

test('saveConn clears a token carried over to a different base', () => {
  api.saveConn({ base: HOSTED, token: 'T' });
  api.saveConn({ base: 'http://localhost:9', token: 'T' });
  assert.equal(api.getConn().token, '');
});

test('a link with no parameters changes nothing', () => {
  const r = open('https://x.example/app/#/map');
  assert.equal(r.out.token, '');
  assert.equal(r.calls.length, 0);
});
