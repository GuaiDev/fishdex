// Turns the GET /fishdex payload into what the collection screen shows.
// Pure functions, no React, so they can be tested on their own.

export const FILTERS = ['all', 'caught', 'missing'];

function familyKey(species) {
  return species.family || 'Unclassified';
}

// Every species the screen can show: the whole regional pool, with the user's
// catch attached when there is one, plus any caught species the pool does not
// list (a catch outside the regional list still belongs in the collection).
export function mergeSpecies(caughtSpecies = [], regionSpeciesPool = []) {
  const caughtById = new Map(caughtSpecies.map((s) => [s.id, s]));
  const poolIds = new Set(regionSpeciesPool.map((s) => s.id));
  return [
    ...regionSpeciesPool.map((s) => ({ ...s, caught: caughtById.get(s.id) || null, inPool: true })),
    ...caughtSpecies
      .filter((s) => !poolIds.has(s.id))
      .map((s) => ({ ...s, caught: s, inPool: false })),
  ];
}

// Caught first (most logged first), then the species you have not logged, by name.
function compareSpecies(a, b) {
  if (!!a.caught !== !!b.caught) return a.caught ? -1 : 1;
  if (a.caught) {
    return b.caught.count - a.caught.count || a.commonName.localeCompare(b.commonName);
  }
  return a.commonName.localeCompare(b.commonName);
}

export function filterSpecies(species, filter) {
  const kept = species.filter((s) =>
    filter === 'caught' ? s.caught : filter === 'missing' ? !s.caught : true,
  );
  return kept.sort(compareSpecies);
}

export function familyProgress(species) {
  const families = new Map();
  for (const s of species) {
    const key = familyKey(s);
    if (!families.has(key)) {
      families.set(key, { key, name: s.familyCommonName || 'Other', have: 0, total: 0 });
    }
    const f = families.get(key);
    f.total += 1;
    if (s.caught) f.have += 1;
  }
  // Families you have started first, fullest first; untouched ones after, by name.
  return [...families.values()].sort(
    (a, b) => b.have / b.total - a.have / a.total || b.have - a.have || a.name.localeCompare(b.name),
  );
}

export function collectionStats(species, families) {
  const poolTotal = species.filter((s) => s.inPool).length;
  return {
    caught: species.filter((s) => s.caught).length,
    poolCaught: species.filter((s) => s.inPool && s.caught).length,
    poolTotal,
    familiesComplete: families.filter((f) => f.total > 0 && f.have === f.total).length,
    familiesTotal: families.length,
  };
}

export function initials(name) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
}

export function formatInches(inches) {
  return `${Number.isInteger(inches) ? inches : inches.toFixed(1)} in`;
}
