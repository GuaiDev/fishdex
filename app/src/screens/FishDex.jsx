import { useCallback, useEffect, useMemo, useState } from 'react';
import { AuthError, getFishDex } from '../api.js';
import { PlusIcon } from '../components/icons.jsx';
import {
  collectionStats,
  familyProgress,
  filterSpecies,
  mergeSpecies,
} from '../fishdexModel.js';
import SpeciesCard from './SpeciesCard.jsx';
import SpeciesSheet from './SpeciesSheet.jsx';
import './fishdex.css';

const FILTER_LABELS = { all: 'All', caught: 'Caught', missing: 'Missing' };

function subheadFor(caught, caughtFamilies) {
  if (caught === 0) return 'Nothing logged yet. Your first catch starts the collection.';
  const families = caughtFamilies === 1 ? 'family' : 'families';
  return `${caught} species across ${caughtFamilies} ${families}.`;
}

function Header({ subhead, onAddCatch }) {
  return (
    <header className="nav">
      <div className="nav-top">
        <span className="nav-meta">Ontario · {new Date().getFullYear()} season</span>
        <button type="button" className="nav-btn" aria-label="Add a catch" onClick={onAddCatch}>
          <span>
            <PlusIcon />
          </span>
        </button>
      </div>
      <h1 className="large-title">FishDex</h1>
      <p className="subhead">{subhead}</p>
    </header>
  );
}

function Loading({ onAddCatch }) {
  return (
    <div aria-busy="true">
      <Header subhead="Loading your collection…" onAddCatch={onAddCatch} />
      <div className="skeleton stats-skeleton" />
      <div className="grid">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="skeleton card-skeleton" />
        ))}
      </div>
    </div>
  );
}

function LoadError({ message, onRetry, onAddCatch }) {
  return (
    <div>
      <Header subhead="Your collection" onAddCatch={onAddCatch} />
      <div className="notice" role="alert">
        <h2>Could not load your FishDex</h2>
        <p>{message}</p>
        <button type="button" className="btn btn-secondary" onClick={onRetry}>
          Try again
        </button>
      </div>
    </div>
  );
}

export default function FishDex({ onAddCatch, onSignedOut }) {
  const [state, setState] = useState({ status: 'loading' });
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState(null);

  const load = useCallback(() => {
    setState({ status: 'loading' });
    getFishDex().then(
      (data) => setState({ status: 'ready', data }),
      (err) => {
        if (err instanceof AuthError) onSignedOut();
        else setState({ status: 'error', message: err.message });
      },
    );
  }, [onSignedOut]);

  useEffect(load, [load]);

  const model = useMemo(() => {
    if (state.status !== 'ready') return null;
    const all = mergeSpecies(state.data.caughtSpecies, state.data.regionSpeciesPool);
    const families = familyProgress(all);
    const caughtFamilies = new Set(all.filter((s) => s.caught).map((s) => s.family || 'Unclassified'));
    return { all, families, stats: collectionStats(all, families), caughtFamilies: caughtFamilies.size };
  }, [state]);

  if (state.status === 'loading') return <Loading onAddCatch={onAddCatch} />;
  if (state.status === 'error') {
    return <LoadError message={state.message} onRetry={load} onAddCatch={onAddCatch} />;
  }

  const { all, families, stats, caughtFamilies } = model;
  const shown = filterSpecies(all, filter);
  const shownCaught = shown.filter((s) => s.caught).length;

  return (
    <div>
      <Header subhead={subheadFor(stats.caught, caughtFamilies)} onAddCatch={onAddCatch} />

      <div className="segmented" role="group" aria-label="Show species">
        {Object.entries(FILTER_LABELS).map(([id, label]) => (
          <button
            key={id}
            type="button"
            aria-pressed={filter === id}
            className={filter === id ? 'on' : undefined}
            onClick={() => setFilter(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <dl className="stats">
        <div className="stat">
          <dt>Species caught</dt>
          <dd className="num">{stats.caught}</dd>
        </div>
        <div className="stat">
          <dt>Regional pool</dt>
          <dd className="num">
            {stats.poolCaught}
            {stats.poolTotal > 0 && <small>/{stats.poolTotal}</small>}
          </dd>
        </div>
        <div className="stat">
          <dt>Families complete</dt>
          <dd className="num">
            {stats.familiesComplete}
            {stats.familiesTotal > 0 && <small>/{stats.familiesTotal}</small>}
          </dd>
        </div>
      </dl>

      <div className="sec-h">
        <h2>Species</h2>
        <span>
          {filter === 'missing'
            ? `${shown.length} not logged`
            : `${shownCaught} of ${shown.length} caught`}
        </span>
      </div>

      {shown.length === 0 ? (
        <p className="empty">
          {all.length === 0
            ? 'No species to show yet. Once the regional species list loads or you log a catch, they appear here.'
            : filter === 'caught'
              ? 'Nothing caught yet. Log a catch and it shows up here.'
              : 'You have caught every species in the regional pool.'}
        </p>
      ) : (
        <ul className="grid">
          {shown.map((s) => (
            <li key={s.id}>
              <SpeciesCard species={s} onOpen={() => setSelected(s)} />
            </li>
          ))}
        </ul>
      )}

      {families.length > 0 && (
        <>
          <div className="sec-h sec-h-spaced">
            <h2>Families</h2>
          </div>
          <ul className="group">
            {families.map((f) => (
              <li key={f.key} className="row">
                <div className="top">
                  <span className="fname">{f.name}</span>
                  <span className="frac num">
                    {f.have} of {f.total}
                  </span>
                </div>
                <div className="bar" aria-hidden="true">
                  <i style={{ width: `${Math.round((f.have / f.total) * 100)}%` }} />
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {selected && <SpeciesSheet species={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
