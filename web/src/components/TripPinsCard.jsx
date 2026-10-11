import { useState } from 'react';
import { createPin, linkStopToPin, setTripPinRating, unlinkStopFromPin, updateSession } from '../api';
import { formatDistance, pinUi, usePinLabels } from '../pins';
import RatingSlider from './RatingSlider';

// After "Log trip": where each stop landed among the user's pins, and the
// optional rating. A trip that touched one pin (or none) gets one slider,
// which is the trip's rating and that pin's. A trip across several pins is
// asked once per pin; any left untouched stay unrated.
export default function TripPinsCard({ sessionId, initialLinks }) {
  const labels = usePinLabels();
  const [entries, setEntries] = useState(() => (initialLinks || []).map(e => ({ ...e, undone: null })));
  const [tripRating, setTripRating] = useState(null);
  const [pinRatings, setPinRatings] = useState({});
  const [naming, setNaming] = useState(null); // stop_id being named
  const [name, setName] = useState('');
  const [error, setError] = useState('');

  if (!labels) return null;

  const linkedPins = [];
  for (const e of entries) {
    if (e.link && !linkedPins.some(p => p.pin_id === e.link.pin_id)) linkedPins.push(e.link);
  }

  function patchEntry(stopId, patch) {
    setEntries(prev => prev.map(e => (e.stop_id === stopId ? { ...e, ...patch } : e)));
  }

  async function run(fn) {
    setError('');
    try { await fn(); } catch (err) { setError(err.message); }
  }

  const rateTrip = value => run(async () => {
    await updateSession(sessionId, { rating: value });
    setTripRating(value);
  });

  const ratePin = (pinId, value) => run(async () => {
    await setTripPinRating(sessionId, pinId, value);
    setPinRatings(prev => ({ ...prev, [pinId]: value }));
  });

  const undo = e => run(async () => {
    await unlinkStopFromPin(e.link.pin_id, e.stop_id);
    patchEntry(e.stop_id, { link: null, undone: e.link });
  });

  const pick = (e, c) => run(async () => {
    await linkStopToPin(c.pin_id, e.stop_id);
    patchEntry(e.stop_id, { link: { pin_id: c.pin_id, name: c.name, kind: 'confirmed', distance_m: c.distance_m }, undone: null });
  });

  const pinThisSpot = e => run(async () => {
    const created = await createPin({ name: name.trim(), lat: e.lat, lng: e.lng, from_stop_id: e.stop_id });
    patchEntry(e.stop_id, { link: { pin_id: created.pin.id, name: created.pin.name, kind: 'confirmed' }, pin_prompt: false });
    setNaming(null);
  });

  return (
    <div style={{
      marginTop: 12, padding: '14px 16px', display: 'grid', gap: 12,
      background: 'var(--fx-card-fill)', border: '1px solid var(--fx-hairline)', borderRadius: 14,
    }}>
      {linkedPins.length <= 1 ? (
        <RatingSlider value={tripRating} onChange={rateTrip} />
      ) : (
        linkedPins.map(p => (
          <RatingSlider
            key={p.pin_id}
            label={`How was ${p.name}?`}
            value={pinRatings[p.pin_id] ?? null}
            onChange={v => ratePin(p.pin_id, v)}
          />
        ))
      )}

      {entries.map(e => (
        <div key={e.stop_id} style={{ display: 'grid', gap: 6 }}>
          {e.link && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', ...pinUi.muted }}>
              <span>
                📍 <span style={{ color: 'var(--fx-text-primary)' }}>{e.link.name}</span>
                {' · '}{labels.link_kind_labels[e.link.kind]}
                {e.link.distance_m != null && ` · ${formatDistance(e.link.distance_m)}`}
              </span>
              {e.link.kind === 'inferred' && (
                <button type="button" style={{ ...pinUi.button, padding: '4px 10px' }} onClick={() => undo(e)}>Undo</button>
              )}
            </div>
          )}

          {!e.link && (e.candidates.length > 0 || e.undone) && (
            <div style={{ display: 'grid', gap: 6 }}>
              <span style={pinUi.muted}>
                {e.candidates.length > 1 ? 'Which of your spots was this?' : 'Link this stop to a spot?'}
              </span>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {(e.candidates.length ? e.candidates : [{ pin_id: e.undone.pin_id, name: e.undone.name, distance_m: e.undone.distance_m }]).map(c => (
                  <button key={c.pin_id} type="button" style={pinUi.button} onClick={() => pick(e, c)}>
                    {c.name}{c.distance_m != null && ` · ${formatDistance(c.distance_m)}`}
                  </button>
                ))}
              </div>
            </div>
          )}

          {!e.link && e.lat != null && (
            naming === e.stop_id ? (
              <div style={{ display: 'flex', gap: 8 }}>
                <input style={pinUi.input} value={name} onChange={ev => setName(ev.target.value)} placeholder="Name this spot" aria-label="Pin name" />
                <button type="button" style={pinUi.primary} disabled={!name.trim()} onClick={() => pinThisSpot(e)}>Save</button>
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <button
                  type="button"
                  style={e.pin_prompt ? pinUi.primary : pinUi.button}
                  onClick={() => { setName(e.location_name || ''); setNaming(e.stop_id); }}
                >
                  📍 Pin this spot
                </button>
                {e.pin_prompt && <span style={pinUi.muted}>You've fished within {labels.link_radius_m} m of here before.</span>}
              </div>
            )
          )}
        </div>
      ))}
      {error && <div style={pinUi.error}>{error}</div>}
    </div>
  );
}
