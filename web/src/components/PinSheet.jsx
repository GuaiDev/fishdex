import { useEffect, useState } from 'react';
import {
  addRecalledVisit, deletePin, deleteRecalledVisit, getPin, mergePin, unlinkStopFromPin, updatePin,
} from '../api';
import { pinUi, usePinLabels } from '../pins';
import RatingSlider from './RatingSlider';

function Fact({ label, value, note }) {
  return (
    <div>
      <div style={{ fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 18, color: 'var(--fx-text-primary-2)' }}>
        {value}
      </div>
      <div style={{ ...pinUi.muted, fontSize: 10, fontWeight: 600, letterSpacing: '.06em', textTransform: 'uppercase' }}>
        {label}
      </div>
      {note && <div style={{ ...pinUi.muted, fontSize: 10 }}>{note}</div>}
    </div>
  );
}

// "I've fished here before": recalled history without a full trip. Stored
// apart from logged trips and never counted in any fact above.
export function RecalledForm({ onSubmit, onCancel, submitLabel = 'Add recalled visit' }) {
  const [dateApprox, setDateApprox] = useState('');
  const [species, setSpecies] = useState('');
  const [rating, setRating] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function submit() {
    setSaving(true);
    setError('');
    try {
      await onSubmit({
        date_approx: dateApprox.trim() || null,
        species: species.split(',').map(s => s.trim()).filter(Boolean),
        rating,
      });
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <input style={pinUi.input} placeholder="Roughly when? e.g. summer 2019" value={dateApprox} onChange={e => setDateApprox(e.target.value)} />
      <input style={pinUi.input} placeholder="Species, comma separated (optional)" value={species} onChange={e => setSpecies(e.target.value)} />
      <RatingSlider value={rating} onChange={setRating} label="How was it? (optional)" />
      <div style={{ display: 'flex', gap: 8 }}>
        <button type="button" style={pinUi.primary} disabled={saving} onClick={submit}>{submitLabel}</button>
        {onCancel && <button type="button" style={pinUi.button} onClick={onCancel}>Cancel</button>}
      </div>
      {error && <div style={pinUi.error}>{error}</div>}
    </div>
  );
}

function formatSize(cm) {
  return `${(cm / 2.54).toFixed(1)}″`;
}

export default function PinSheet({ pinId, otherPins, onChanged, onClose }) {
  const labels = usePinLabels();
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  const [mode, setMode] = useState(null); // null | 'rename' | 'notes' | 'recalled' | 'merge' | 'delete'
  const [draft, setDraft] = useState('');
  const [mergeInto, setMergeInto] = useState('');

  async function load() {
    try {
      setDetail(await getPin(pinId));
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { setDetail(null); setMode(null); load(); }, [pinId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(fn, { close = false } = {}) {
    try {
      await fn();
      setMode(null);
      onChanged();
      if (close) onClose(); else await load();
    } catch (err) {
      setError(err.message);
    }
  }

  if (error && !detail) return <div style={pinUi.error}>{error}</div>;
  if (!detail || !labels) return <div style={pinUi.muted}>Loading…</div>;

  const { pin, facts, trips, recalled } = detail;
  const merges = otherPins.filter(p => p.id !== pin.id);

  return (
    <div style={{ maxHeight: '60dvh', overflowY: 'auto', paddingRight: 28 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
        <span style={pinUi.heading}>{pin.name}</span>
        <span style={pinUi.tag}>{labels.state_labels[pin.state]}</span>
        {pin.has_recalled && <span style={{ ...pinUi.tag, fontStyle: 'italic' }}>{labels.recalled_marker}</span>}
        <button
          type="button"
          aria-pressed={pin.is_favourite}
          aria-label={pin.is_favourite ? 'Remove from favourites' : 'Add to favourites'}
          onClick={() => act(() => updatePin(pin.id, { is_favourite: !pin.is_favourite }))}
          style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, padding: 0,
            color: pin.is_favourite ? 'var(--fx-brass-star)' : 'var(--fx-text-muted)' }}
        >
          {pin.is_favourite ? '★' : '☆'}
        </button>
      </div>
      {pin.notes && mode !== 'notes' && (
        <div style={{ ...pinUi.muted, whiteSpace: 'pre-wrap', marginBottom: 8 }}>{pin.notes}</div>
      )}

      {/* Facts from logged trips only. No rating appears here: ratings
          belong to trips and are never averaged into the place. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, margin: '10px 0' }}>
        <Fact label="Visits" value={facts.visits} />
        <Fact label="Fish logged" value={facts.fish_logged} />
        <Fact
          label="Fish / hour"
          value={facts.fish_per_hour ?? '—'}
          note={facts.fish_per_hour != null ? `${facts.fish_per_hour_trips} timed trip${facts.fish_per_hour_trips === 1 ? '' : 's'}` : null}
        />
        <Fact label="Last visit" value={facts.last_visit || '—'} />
        <Fact
          label="Biggest"
          value={facts.biggest_fish ? formatSize(facts.biggest_fish.size_cm) : '—'}
          note={facts.biggest_fish?.species || null}
        />
        <Fact label="Species" value={facts.species.length} />
      </div>
      {facts.species.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10 }}>
          {facts.species.map(sp => (
            <span key={sp} style={{ background: 'var(--fx-card-fill-quiet)', color: 'var(--fx-moss-light)', fontFamily: 'var(--fx-font-ui)', borderRadius: 6, padding: '3px 8px', fontSize: 11 }}>{sp}</span>
          ))}
        </div>
      )}

      {trips.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ ...pinUi.muted, fontWeight: 600, marginBottom: 4 }}>Trips</div>
          {trips.map(t => (
            <div key={t.session_id} style={{ borderTop: '1px solid var(--fx-hairline)', padding: '6px 0' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', ...pinUi.muted }}>
                <span style={{ color: 'var(--fx-text-primary)' }}>{t.date || t.date_approx || 'Undated'}</span>
                <span>
                  {t.fish} fish · {t.rating != null ? labels.rating_labels[String(t.rating)] : 'not rated'}
                </span>
              </div>
              {t.stops.map(s => (
                <div key={s.stop_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', ...pinUi.muted, fontSize: 11 }}>
                  <span>{s.location || 'stop'} · {labels.link_kind_labels[s.kind]}</span>
                  {s.kind === 'inferred' && (
                    <button type="button" style={{ ...pinUi.button, padding: '3px 8px', fontSize: 11 }}
                      onClick={() => act(() => unlinkStopFromPin(pin.id, s.stop_id))}>
                      Undo
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {recalled.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ ...pinUi.muted, fontWeight: 600, marginBottom: 4 }}>
            Recalled <span style={{ fontWeight: 400 }}>— not counted in the facts above</span>
          </div>
          {recalled.map(r => (
            <div key={r.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid var(--fx-hairline)', padding: '6px 0', ...pinUi.muted }}>
              <span>
                <span style={{ ...pinUi.tag, fontStyle: 'italic', marginRight: 6 }}>{labels.recalled_marker}</span>
                {r.date_approx || 'undated'}
                {r.species.length > 0 && ` · ${r.species.join(', ')}`}
                {r.rating != null && ` · ${labels.rating_labels[String(r.rating)]}`}
              </span>
              <button type="button" aria-label="Remove recalled visit" style={{ border: 'none', background: 'none', color: 'var(--fx-text-muted)', cursor: 'pointer' }}
                onClick={() => act(() => deleteRecalledVisit(pin.id, r.id))}>×</button>
            </div>
          ))}
        </div>
      )}

      {mode === 'rename' && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <input style={pinUi.input} value={draft} onChange={e => setDraft(e.target.value)} aria-label="Pin name" />
          <button type="button" style={pinUi.primary} disabled={!draft.trim()} onClick={() => act(() => updatePin(pin.id, { name: draft.trim() }))}>Save</button>
        </div>
      )}
      {mode === 'notes' && (
        <div style={{ display: 'grid', gap: 8, marginBottom: 8 }}>
          <textarea style={{ ...pinUi.input, minHeight: 70 }} value={draft} onChange={e => setDraft(e.target.value)} aria-label="Pin notes" />
          <div><button type="button" style={pinUi.primary} onClick={() => act(() => updatePin(pin.id, { notes: draft.trim() || null }))}>Save notes</button></div>
        </div>
      )}
      {mode === 'recalled' && (
        <div style={{ marginBottom: 8 }}>
          <RecalledForm onSubmit={fields => act(() => addRecalledVisit(pin.id, fields))} onCancel={() => setMode(null)} />
        </div>
      )}
      {mode === 'merge' && (
        <div style={{ display: 'grid', gap: 8, marginBottom: 8 }}>
          <div style={pinUi.muted}>Move this pin's trips and recalled visits to another pin, then remove this one.</div>
          <select style={pinUi.input} value={mergeInto} onChange={e => setMergeInto(e.target.value)} aria-label="Merge into">
            <option value="">Choose a pin…</option>
            {merges.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <div><button type="button" style={pinUi.primary} disabled={!mergeInto}
            onClick={() => act(() => mergePin(pin.id, Number(mergeInto)), { close: true })}>Merge</button></div>
        </div>
      )}
      {mode === 'delete' && (
        <div style={{ display: 'grid', gap: 8, marginBottom: 8 }}>
          <div style={pinUi.muted}>
            Delete this pin? {facts.visits === 1 ? 'Its 1 trip stays' : `Its ${facts.visits} trips stay`} in your log, unpinned.
            {recalled.length > 0 && ' Its recalled visits are deleted with it.'}
          </div>
          <div><button type="button" style={{ ...pinUi.primary, background: 'var(--color-rust)' }}
            onClick={() => act(() => deletePin(pin.id), { close: true })}>Delete pin</button></div>
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        <button type="button" style={pinUi.button} onClick={() => setMode('recalled')}>I've fished here before</button>
        <button type="button" style={pinUi.button} onClick={() => { setDraft(pin.name); setMode('rename'); }}>Rename</button>
        <button type="button" style={pinUi.button} onClick={() => { setDraft(pin.notes || ''); setMode('notes'); }}>Notes</button>
        {merges.length > 0 && <button type="button" style={pinUi.button} onClick={() => setMode('merge')}>Merge…</button>}
        <button type="button" style={pinUi.button} onClick={() => setMode('delete')}>Delete…</button>
      </div>
      {error && <div style={pinUi.error}>{error}</div>}
    </div>
  );
}
