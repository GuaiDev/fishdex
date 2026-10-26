import test from 'node:test';
import assert from 'node:assert/strict';
import {
  collectionStats,
  familyProgress,
  filterSpecies,
  formatInches,
  initials,
  mergeSpecies,
} from './fishdexModel.js';

const sp = (id, commonName, family, extra = {}) => ({
  id,
  commonName,
  family,
  familyCommonName: family,
  ...extra,
});

const pool = [
  sp('a', 'Walleye', 'Perches'),
  sp('b', 'Yellow Perch', 'Perches'),
  sp('c', 'Northern Pike', 'Pikes'),
];
const caught = [
  sp('a', 'Walleye', 'Perches', { count: 7 }),
  sp('b', 'Yellow Perch', 'Perches', { count: 9 }),
  sp('z', 'Goldfish', 'Carps', { count: 1 }),
];

test('mergeSpecies attaches catches and keeps catches outside the pool', () => {
  const all = mergeSpecies(caught, pool);
  assert.equal(all.length, 4);
  assert.equal(all.find((s) => s.id === 'c').caught, null);
  assert.equal(all.find((s) => s.id === 'z').inPool, false);
});

test('mergeSpecies tolerates an empty collection', () => {
  assert.deepEqual(mergeSpecies([], []), []);
  assert.equal(mergeSpecies([], pool).every((s) => !s.caught), true);
});

test('filterSpecies splits caught from missing and puts caught first', () => {
  const all = mergeSpecies(caught, pool);
  assert.deepEqual(filterSpecies(all, 'caught').map((s) => s.id), ['b', 'a', 'z']);
  assert.deepEqual(filterSpecies(all, 'missing').map((s) => s.id), ['c']);
  assert.deepEqual(filterSpecies(all, 'all').map((s) => s.id), ['b', 'a', 'z', 'c']);
});

test('familyProgress counts caught species per family, fullest first', () => {
  const families = familyProgress(mergeSpecies(caught, pool));
  assert.deepEqual(
    families.map((f) => [f.name, f.have, f.total]),
    [['Perches', 2, 2], ['Carps', 1, 1], ['Pikes', 0, 1]],
  );
});

test('collectionStats separates all catches from regional-pool catches', () => {
  const all = mergeSpecies(caught, pool);
  assert.deepEqual(collectionStats(all, familyProgress(all)), {
    caught: 3,
    poolCaught: 2,
    poolTotal: 3,
    familiesComplete: 2,
    familiesTotal: 3,
  });
});

test('collectionStats on an empty collection is all zeros', () => {
  assert.deepEqual(collectionStats([], []), {
    caught: 0,
    poolCaught: 0,
    poolTotal: 0,
    familiesComplete: 0,
    familiesTotal: 0,
  });
});

test('initials and formatInches', () => {
  assert.equal(initials('Smallmouth Bass'), 'SB');
  assert.equal(initials('Walleye'), 'W');
  assert.equal(formatInches(19.5), '19.5 in');
  assert.equal(formatInches(20), '20 in');
});
