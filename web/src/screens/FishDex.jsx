import { useState, useEffect, useMemo } from 'react';
import { getFishDex, resolvePhotoUrl } from '../api';
import '../manual-tokens.css';

// ============================================================================
// My FishDex — the collection screen, rebuilt on the "Field Manual" system
// (web/src/manual-tokens.css).
//
// Same data logic and adaptive layout as before: the number of distinct
// families in the user's real catches decides the view — < 3 renders the
// flat list of caught species, >= 3 folds species into family folders with
// one expanded. Computed live from GET /fishdex, never a user toggle.
//
// What changed is the register: high-contrast paper/ink, one sans + mono
// for figures and scientific names, flat hairline-surfaced cells with a
// solid paper caption band over photos (no gradient scrim, no glass, no
// grain), typographic PB/NEW tags instead of gold pills and glyphs, no
// eyebrow/badge above the title, no fade-in wrapper, no glow pin on the
// progress rail.
//
// Data-shape notes (unchanged from the prior build):
// - getFishDex() returns { caughtSpecies, regionSpeciesPool }. Pool species
//   carry identity (commonName, scientificName, family, familyCommonName);
//   caught records carry stats (count, isPersonalBest, isNewFind,
//   maxLengthInches, representativePhotoUrl). A caught species not listed in
//   the pool keeps its identity fields.
// - biggest_size (PB length) has no populating input path yet, so a species
//   with no recorded size shows a plain count and no "PB" figure.
// - A species caught exactly once stands in for "new find" (no
//   acknowledgment-state table exists).
// - Region title reads "Ontario", the real jurisdiction this data is scoped
//   to, as before.
// ============================================================================

function familyKeyOf(species) {
  return species.family || 'Unclassified';
}

// Merge caughtSpecies + regionSpeciesPool into one per-family structure:
// each family carries every species (caught + locked) it contains, plus
// caught/total counts and a representative photo for the collapsed banner.
function groupByFamily(caughtSpecies, regionSpeciesPool) {
  const caughtById = new Map(caughtSpecies.map(s => [s.id, s]));
  const poolIds = new Set(regionSpeciesPool.map(s => s.id));

  const allSpecies = [
    ...regionSpeciesPool.map(s => ({ ...s, caught: caughtById.get(s.id) || null })),
    ...caughtSpecies.filter(s => !poolIds.has(s.id)).map(s => ({ ...s, caught: s })),
  ];

  const families = new Map();
  for (const sp of allSpecies) {
    const key = familyKeyOf(sp);
    if (!families.has(key)) {
      families.set(key, {
        familyKey: key,
        familyCommonName: sp.familyCommonName || 'Other',
        species: [],
      });
    }
    families.get(key).species.push(sp);
  }

  return Array.from(families.values()).map(f => {
    const caughtCount = f.species.filter(s => s.caught).length;
    const sortedSpecies = [...f.species].sort((a, b) => {
      if (!!a.caught !== !!b.caught) return a.caught ? -1 : 1;
      if (a.caught && b.caught) {
        if (a.caught.isPersonalBest !== b.caught.isPersonalBest) return a.caught.isPersonalBest ? -1 : 1;
        return b.caught.count - a.caught.count;
      }
      return a.commonName.localeCompare(b.commonName);
    });
    return {
      ...f,
      species: sortedSpecies,
      caughtCount,
      totalCount: f.species.length,
      hasNewFind: f.species.some(s => s.caught?.isNewFind),
      representativePhotoUrl:
        f.species.find(s => s.caught?.isPersonalBest && s.caught?.representativePhotoUrl)?.caught
          ?.representativePhotoUrl ||
        f.species.find(s => s.caught?.representativePhotoUrl)?.caught?.representativePhotoUrl ||
        null,
    };
  });
}

// ── shared bits ───────────────────────────────────────────────────────────────

const sans = 'var(--fm-sans)';
const mono = 'var(--fm-mono)';
// Flat hatched "empty" tile — the chart convention for a slot with nothing
// in it yet. Legible, quiet, and not illustrated-fish artwork. Callers opt
// into a dashed edge only for thumbnail tiles; full-bleed photo slots are
// framed by their card's own border.
function EmptyTile({ label, dashed }) {
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        background: 'repeating-linear-gradient(45deg, var(--fm-quiet) 0 6px, var(--fm-paper) 6px 12px)',
        border: dashed ? '1px dashed var(--fm-hairline-strong)' : 'none',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: mono,
        fontSize: 10,
        color: 'var(--fm-ink-3)',
        letterSpacing: '0.04em',
      }}
    >
      {label || 'no photo'}
    </div>
  );
}

// Solid paper caption band under a photo — replaces the gradient scrim.
// Name + tag on the first line, scientific name + figure on the second.
function Caption({ children, caption, figure, figureStrong }) {
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ fontFamily: sans, fontWeight: 600, fontSize: 15, color: 'var(--fm-ink)' }}>
          {children}
        </div>
        {figure && (
          <div
            style={{
              fontFamily: mono,
              fontWeight: 600,
              fontSize: 14,
              color: figureStrong ? 'var(--fm-chart)' : 'var(--fm-ink)',
              whiteSpace: 'nowrap',
            }}
          >
            {figure}
          </div>
        )}
      </div>
      {caption && (
        <div
          style={{
            marginTop: 3,
            fontFamily: mono,
            fontSize: 11,
            color: 'var(--fm-ink-3)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          {caption}
        </div>
      )}
    </>
  );
}

function statText(c) {
  if (c.isPersonalBest && c.maxLengthInches) {
    return { figure: `${Math.round(c.maxLengthInches)} in`, caption: 'personal best', strong: true };
  }
  return { figure: `${c.count}`, caption: 'logged', strong: false };
}

// ── header ────────────────────────────────────────────────────────────────────

function Header({ discovered, total, distinctFamilyCount, isGrouped }) {
  const pct = total > 0 ? Math.min(100, (discovered / total) * 100) : 0;

  return (
    <div style={{
      padding: '20px 16px 16px',
      borderBottom: '1px solid var(--fm-hairline)',
      background: 'var(--fm-paper)',
      flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <h1 style={{ fontFamily: sans, fontWeight: 600, fontSize: 24, color: 'var(--fm-ink)', margin: 0, lineHeight: 1.15 }}>
          Ontario
        </h1>
        <div style={{ fontFamily: mono, fontSize: 12, color: 'var(--fm-ink-3)' }}>
          {total} species{isGrouped ? ` · ${distinctFamilyCount} families` : ''}
        </div>
      </div>

      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={discovered}
        style={{ height: 4, marginTop: 14, background: 'var(--fm-quiet)', borderRadius: 2 }}
      >
        <div style={{ height: '100%', width: `${pct}%`, background: 'var(--fm-chart)', borderRadius: 2 }} />
      </div>

      {!isGrouped && (
        <div style={{ marginTop: 8, fontFamily: mono, fontSize: 11, color: 'var(--fm-ink-3)' }}>
          {discovered} of {total} species caught
        </div>
      )}
    </div>
  );
}

// ── flat view ─────────────────────────────────────────────────────────────────

function CaughtPlate({ species }) {
  const c = species.caught;
  const photoUrl = c.representativePhotoUrl ? resolvePhotoUrl(c.representativePhotoUrl) : null;
  const { figure, caption, strong } = statText(c);
  const tag = c.isPersonalBest && c.maxLengthInches ? 'PB' : c.isNewFind ? 'new' : null;

  return (
    <div style={{
      background: 'var(--fm-panel)',
      border: '1px solid var(--fm-hairline)',
      borderRadius: 'var(--fm-r-md)',
      overflow: 'hidden',
      marginBottom: 12,
    }}>
      <div style={{ height: 170, background: 'var(--fm-quiet)' }}>
        {photoUrl ? (
          <img src={photoUrl} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
        ) : (
          <EmptyTile />
        )}
      </div>
      <div style={{ padding: '10px 12px', borderTop: '1px solid var(--fm-hairline)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {tag && <span className={`fm-tag${strong ? ' fm-tag--earned' : ' fm-tag--pending'}`}>{tag}</span>}
          <span style={{ fontFamily: sans, fontWeight: 600, fontSize: 15, color: 'var(--fm-ink)' }}>
            {species.commonName}
          </span>
        </div>
        <div style={{ marginTop: 3, fontFamily: mono, fontSize: 11, color: 'var(--fm-ink-3)' }}>
          {species.scientificName || 'species'} · {caption} · {figure}
        </div>
      </div>
    </div>
  );
}

function UndiscoveredRow({ species }) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      background: 'var(--fm-panel)',
      border: '1px dashed var(--fm-hairline-strong)',
      borderRadius: 'var(--fm-r-md)',
      padding: 10,
      marginBottom: 8,
    }}>
      <div style={{ width: 44, height: 44, flexShrink: 0, borderRadius: 'var(--fm-r-sm)', overflow: 'hidden' }}>
        <EmptyTile label="—" />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontFamily: sans, fontWeight: 500, fontSize: 14, color: 'var(--fm-ink)' }}>
          {species.commonName}
        </div>
        {species.scientificName && (
          <div style={{ fontFamily: mono, fontSize: 10.5, color: 'var(--fm-ink-3)' }}>{species.scientificName}</div>
        )}
      </div>
      <div style={{ fontFamily: mono, fontSize: 10.5, color: 'var(--fm-ink-3)', flexShrink: 0 }}>not caught yet</div>
    </div>
  );
}

function FlatView({ caughtSpecies, undiscoveredSpecies }) {
  const sorted = useMemo(() => [...caughtSpecies].sort((a, b) => {
    const ac = a.caught, bc = b.caught;
    if (ac.isPersonalBest !== bc.isPersonalBest) return ac.isPersonalBest ? -1 : 1;
    return bc.count - ac.count;
  }), [caughtSpecies]);

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '16px 16px 30px' }}>
      {sorted.map(sp => <CaughtPlate key={sp.id} species={sp} />)}

      {undiscoveredSpecies.length > 0 && (
        <>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10, margin: '4px 0 14px',
            fontFamily: mono, fontSize: 10.5, letterSpacing: '0.06em', textTransform: 'uppercase',
            color: 'var(--fm-ink-3)',
          }}>
            <span>Not caught yet</span>
            <div style={{ flex: 1, height: 1, background: 'var(--fm-hairline)' }} />
            <span>{undiscoveredSpecies.length}</span>
          </div>
          {undiscoveredSpecies.map(sp => <UndiscoveredRow key={sp.id} species={sp} />)}
        </>
      )}
    </div>
  );
}

// ── grouped view ──────────────────────────────────────────────────────────────

function FamilyGridCell({ species }) {
  if (!species.caught) {
    return (
      <div style={{
        background: 'var(--fm-panel)',
        border: '1px dashed var(--fm-hairline-strong)',
        borderRadius: 'var(--fm-r-sm)',
        overflow: 'hidden',
      }}>
        <div style={{ height: 90, background: 'var(--fm-paper)' }}>
          <EmptyTile label="not caught yet" style={{ border: 'none' }} />
        </div>
        <div style={{ padding: '8px 10px' }}>
          <div style={{ fontFamily: sans, fontWeight: 500, fontSize: 13, color: 'var(--fm-ink-2)' }}>
            {species.commonName}
          </div>
        </div>
      </div>
    );
  }

  const c = species.caught;
  const photoUrl = c.representativePhotoUrl ? resolvePhotoUrl(c.representativePhotoUrl) : null;
  const { figure, caption, strong } = statText(c);

  return (
    <div style={{
      background: 'var(--fm-panel)',
      border: `1px solid ${c.isPersonalBest && c.maxLengthInches ? 'var(--fm-chart)' : 'var(--fm-hairline)'}`,
      borderRadius: 'var(--fm-r-sm)',
      overflow: 'hidden',
    }}>
      <div style={{ height: 90, background: 'var(--fm-quiet)' }}>
        {photoUrl ? (
          <img src={photoUrl} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
        ) : (
          <EmptyTile />
        )}
      </div>
      <div style={{ padding: '8px 10px' }}>
        <div style={{ fontFamily: sans, fontWeight: 500, fontSize: 13, color: 'var(--fm-ink)' }}>
          {species.commonName}
        </div>
        <div style={{ marginTop: 2, fontFamily: mono, fontSize: 10, color: strong ? 'var(--fm-chart)' : 'var(--fm-ink-3)' }}>
          {caption} · {figure}
        </div>
      </div>
    </div>
  );
}

function FamilyFolder({ family, expanded, onToggle }) {
  const photoUrl = family.representativePhotoUrl ? resolvePhotoUrl(family.representativePhotoUrl) : null;

  return (
    <div style={{ marginBottom: 12, background: 'var(--fm-panel)', border: '1px solid var(--fm-hairline)', borderRadius: 'var(--fm-r-md)', overflow: 'hidden' }}>
      <button
        type="button"
        onClick={onToggle}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          width: '100%',
          padding: 10,
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
          font: 'inherit',
        }}
      >
        <div style={{ width: 56, height: 56, flexShrink: 0, borderRadius: 'var(--fm-r-sm)', overflow: 'hidden' }}>
          {photoUrl ? (
            <img src={photoUrl} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
          ) : (
            <EmptyTile label="—" />
          )}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: sans, fontWeight: 600, fontSize: 15, color: 'var(--fm-ink)' }}>
            {family.familyCommonName}
            {family.hasNewFind && <span style={{ fontFamily: mono, fontSize: 10, color: 'var(--fm-chart)' }}> · new</span>}
          </div>
          <div style={{ marginTop: 2, fontFamily: mono, fontSize: 11, color: 'var(--fm-ink-3)' }}>
            {family.familyKey} · {family.caughtCount} of {family.totalCount}
          </div>
        </div>
        <div style={{ fontFamily: mono, fontSize: 14, color: 'var(--fm-ink-2)' }} aria-hidden="true">
          {expanded ? '▾' : '▸'}
        </div>
      </button>

      {expanded && (
        <div style={{ padding: '0 10px 12px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {family.species.map(sp => <FamilyGridCell key={sp.id} species={sp} />)}
        </div>
      )}
    </div>
  );
}

function ExploreCard({ family, onExpand }) {
  return (
    <button
      type="button"
      onClick={onExpand}
      style={{
        flexShrink: 0,
        width: 132,
        background: 'var(--fm-panel)',
        border: '1px dashed var(--fm-hairline-strong)',
        borderRadius: 'var(--fm-r-sm)',
        padding: 10,
        textAlign: 'left',
        cursor: 'pointer',
        font: 'inherit',
      }}
    >
      <div style={{ fontFamily: sans, fontWeight: 600, fontSize: 13, color: 'var(--fm-ink)' }}>
        {family.familyCommonName}
      </div>
      <div style={{ marginTop: 2, fontFamily: mono, fontSize: 10.5, color: 'var(--fm-ink-3)' }}>
        0 of {family.totalCount}
      </div>
    </button>
  );
}

function GroupedView({ families }) {
  const touched = useMemo(
    () => [...families].filter(f => f.caughtCount > 0).sort((a, b) => b.caughtCount - a.caughtCount),
    [families]
  );
  const untouched = useMemo(
    () => [...families].filter(f => f.caughtCount === 0).sort((a, b) => a.familyCommonName.localeCompare(b.familyCommonName)),
    [families]
  );
  const [expandedKey, setExpandedKey] = useState(touched[0]?.familyKey ?? untouched[0]?.familyKey ?? null);

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '16px 16px 30px' }}>
      {touched.map(family => (
        <FamilyFolder
          key={family.familyKey}
          family={family}
          expanded={family.familyKey === expandedKey}
          onToggle={() => setExpandedKey(family.familyKey === expandedKey ? null : family.familyKey)}
        />
      ))}

      {untouched.length > 0 && (
        <>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10, margin: '6px 0 14px',
            fontFamily: mono, fontSize: 10.5, letterSpacing: '0.06em', textTransform: 'uppercase',
            color: 'var(--fm-ink-3)',
          }}>
            <span>Families to explore</span>
            <div style={{ flex: 1, height: 1, background: 'var(--fm-hairline)' }} />
            <span>{untouched.length}</span>
          </div>
          <div style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 4 }}>
            {untouched.map(family => (
              <ExploreCard key={family.familyKey} family={family} onExpand={() => setExpandedKey(family.familyKey)} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ── empty / dev ───────────────────────────────────────────────────────────────

function EmptyState({ onNavigate }) {
  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '32px 32px 64px',
      textAlign: 'center',
    }}>
      <div style={{ fontFamily: sans, fontWeight: 600, fontSize: 20, color: 'var(--fm-ink)' }}>
        No species logged yet
      </div>
      <div style={{
        fontFamily: sans, fontSize: 13, lineHeight: 1.5, color: 'var(--fm-ink-2)',
        maxWidth: 260, margin: '8px 0 20px',
      }}>
        Log a trip with a catch to start your collection. Each species you catch gets a page here.
      </div>
      <button type="button" className="fm-btn" onClick={() => onNavigate?.('log')}>
        Log a catch
      </button>
    </div>
  );
}

function DevViewOverride({ value, onChange }) {
  if (!import.meta.env.DEV) return null;
  const options = [
    { id: 'auto', label: 'Real data' },
    { id: 'empty', label: 'Empty' },
    { id: 'flat', label: 'Flat' },
    { id: 'grouped', label: 'Grouped' },
  ];
  return (
    <div style={{
      position: 'fixed', top: 8, right: 8, zIndex: 200,
      display: 'flex', gap: 2, padding: 2,
      background: 'var(--fm-panel)',
      border: '1px solid var(--fm-hairline)',
      borderRadius: 'var(--fm-r-sm)',
    }}>
      {options.map(opt => (
        <button
          key={opt.id}
          type="button"
          onClick={() => onChange(opt.id)}
          style={{
            font: `500 9px ${mono}`,
            letterSpacing: '0.03em',
            padding: '4px 7px',
            borderRadius: 'var(--fm-r-xs)',
            border: 'none',
            cursor: 'pointer',
            background: value === opt.id ? 'var(--fm-ink)' : 'transparent',
            color: value === opt.id ? 'var(--fm-paper)' : 'var(--fm-ink-2)',
          }}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ── screen ────────────────────────────────────────────────────────────────────

export default function FishDex({ onNavigate }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [devView, setDevView] = useState('auto');

  useEffect(() => {
    getFishDex()
      .then(setData)
      .catch(err => setError(err.message));
  }, []);

  const families = useMemo(
    () => data ? groupByFamily(data.caughtSpecies, data.regionSpeciesPool) : [],
    [data]
  );
  const distinctFamilyCount = useMemo(
    () => families.filter(f => f.caughtCount > 0).length,
    [families]
  );
  const isGrouped = distinctFamilyCount >= 3;

  const caughtSpeciesMerged = useMemo(() => {
    if (!data) return [];
    const poolIds = new Set(data.regionSpeciesPool.map(s => s.id));
    const fromPool = data.regionSpeciesPool
      .filter(s => data.caughtSpecies.some(c => c.id === s.id))
      .map(s => ({ ...s, caught: data.caughtSpecies.find(c => c.id === s.id) }));
    const notInPool = data.caughtSpecies
      .filter(c => !poolIds.has(c.id))
      .map(c => ({ ...c, caught: c }));
    return [...fromPool, ...notInPool];
  }, [data]);

  const undiscoveredSpecies = useMemo(() => {
    if (!data) return [];
    const caughtIds = new Set(data.caughtSpecies.map(c => c.id));
    return data.regionSpeciesPool.filter(s => !caughtIds.has(s.id));
  }, [data]);

  const discovered = data?.caughtSpecies.length ?? 0;
  const total = data ? Math.max(data.regionSpeciesPool.length, discovered) : 0;

  const effectiveIsEmpty = devView === 'empty' ? true : devView === 'auto' ? discovered === 0 : false;
  const effectiveIsGrouped = devView === 'grouped' ? true : devView === 'flat' ? false : isGrouped;

  return (
    <div style={{
      position: 'relative',
      minHeight: '100dvh',
      display: 'flex',
      flexDirection: 'column',
      background: 'var(--fm-paper)',
      color: 'var(--fm-ink)',
      fontFamily: sans,
      paddingBottom: 84,
    }}>
      <DevViewOverride value={devView} onChange={setDevView} />

      {error && (
        <div style={{ padding: 16, fontFamily: sans, fontSize: 13, color: 'var(--fm-alarm)' }}>
          {error.includes('401') ? 'Not authenticated.' : `Couldn't load your FishDex: ${error}`}
        </div>
      )}

      {!error && !data && (
        <div style={{ padding: 16, fontFamily: mono, fontSize: 12, color: 'var(--fm-ink-3)' }}>
          Loading…
        </div>
      )}

      {data && (
        <>
          <Header
            discovered={discovered}
            total={total}
            distinctFamilyCount={distinctFamilyCount}
            isGrouped={effectiveIsGrouped}
          />
          {effectiveIsEmpty ? (
            <EmptyState onNavigate={onNavigate} />
          ) : effectiveIsGrouped ? (
            <GroupedView families={families} />
          ) : (
            <FlatView caughtSpecies={caughtSpeciesMerged} undiscoveredSpecies={undiscoveredSpecies} />
          )}
        </>
      )}
    </div>
  );
}