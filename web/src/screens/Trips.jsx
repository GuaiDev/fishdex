import { useState, useEffect } from 'react';
import { getSessions, resolvePhotoUrl, setTripPinRating, updateSession } from '../api';
import InstrumentDial from '../components/InstrumentDial';
import GrainOverlay from '../components/GrainOverlay';
import RatingSlider from '../components/RatingSlider';
import { isoToLocalInput, localInputToIso, pinUi, usePinLabels } from '../pins';
import '../fishdex-tokens.css';

// Same fish glyph as FishDex's no-photo placeholder (FishDex.jsx doesn't
// export it, so it's duplicated here rather than adding a cross-screen
// export for one shared icon).
const FISH_GLYPH = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path
      d="M3 12c3-4 7-6 11-6 3 0 5.5 2 7 4-1.5 2-4 4-7 4-4 0-8-2-11-6z"
      stroke="currentColor"
      strokeWidth="1.2"
    />
    <path d="M21 10l2 2-2 2" stroke="currentColor" strokeWidth="1.2" />
    <circle cx="8" cy="11" r="0.8" fill="currentColor" />
  </svg>
);

function formatDate(d) {
  if (!d || d === 'Undated') return 'Undated';
  const parsed = new Date(d);
  if (Number.isNaN(parsed.getTime())) return d;
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

// Trip/session card — the same full-bleed photo-plate language as FishDex's
// CaughtPlate (photo fills the card, bottom gradient scrim, text overlaid),
// applied to a session instead of a species: location + species csv on the
// left, date + species count on the right, mirroring CaughtPlate's
// name/scientific-name vs. figure/caption columns exactly.
function TripPlate({ session }) {
  const species = session.species_caught || [];
  const date = session.date || session.date_approx || 'Undated';
  const location = session.location || session.primary_location || 'Unknown location';
  const conditions = session.conditions;
  const hasAnomaly = conditions?.anomaly_flag && conditions.anomaly_flag !== 'normal';
  const thumbnailUrl = (session.catches || []).find(c => c.photo_url)?.photo_url;
  const photoUrl = thumbnailUrl ? resolvePhotoUrl(thumbnailUrl) : null;
  const hasDials = conditions?.air_temp_c != null || conditions?.pressure_hpa != null;

  return (
    <div style={{
      position: 'relative', borderRadius: 18, height: 214, overflow: 'hidden',
      border: '1px solid var(--fx-hairline)', marginBottom: 13, background: 'var(--fx-card-fill-quiet)',
    }}>
      {photoUrl ? (
        <img src={photoUrl} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
      ) : (
        <div style={{
          width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: 'var(--fx-text-locked-3)',
        }}>
          {FISH_GLYPH}
        </div>
      )}

      <div style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(transparent 40%, rgba(11,12,8,.55) 68%, rgba(9,10,6,.9) 100%)',
      }} />

      {/* Overlay Chrome Rule: translucent bark + blur, never solid, for
          chrome floating on top of photography. */}
      {hasDials && (
        <div style={{
          position: 'absolute', top: 10, left: 10, display: 'flex', gap: 4,
          padding: 4, borderRadius: 16,
          background: 'rgba(23,20,15,.55)', backdropFilter: 'blur(4px)',
          transform: 'scale(0.72)', transformOrigin: 'top left',
        }}>
          {conditions.air_temp_c != null && (
            <InstrumentDial value={conditions.air_temp_c} unit="°C" label="Air temp" min={-10} max={35} />
          )}
          {conditions.pressure_hpa != null && (
            <InstrumentDial value={conditions.pressure_hpa} unit="" label="Pressure hPa" min={970} max={1040} />
          )}
        </div>
      )}

      {hasAnomaly && (
        <div style={{
          position: 'absolute', top: 12, right: 12,
          padding: '5px 10px', borderRadius: 14,
          background: 'var(--color-rust-bg)', border: '1px solid var(--color-rust-border)',
        }}>
          <span style={{
            fontFamily: 'var(--fx-font-ui)', fontSize: 10, fontWeight: 600,
            letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--color-rust)',
          }}>
            {conditions.anomaly_flag.replace('_', ' ')}
          </span>
        </div>
      )}

      <div style={{ position: 'absolute', left: 16, right: 16, bottom: 14, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 20, color: 'var(--fx-text-primary-2)' }}>
            {location}
          </div>
          {species.length > 0 && (
            <div style={{
              fontFamily: 'var(--fx-font-serif)', fontStyle: 'italic', fontWeight: 400, fontSize: 13,
              color: 'rgba(194,204,159,.8)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {species.join(', ')}
            </div>
          )}
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0, paddingLeft: 10 }}>
          <div style={{ fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 16, color: 'var(--fx-text-primary-2)' }}>
            {formatDate(date)}
          </div>
          <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 10.5, color: 'rgba(246,241,230,.6)' }}>
            {species.length > 0 ? `${species.length} species` : 'no species logged'}
          </div>
        </div>
      </div>
    </div>
  );
}

// Edit a logged trip: its optional rating (per pin when the trip covered
// several pins) and its start and end times, which fish per hour needs.
// Trips logged before the timer was saved show "—" until times are added.
function TripEditor({ session, onSaved }) {
  const labels = usePinLabels();
  const [started, setStarted] = useState(isoToLocalInput(session.started_at));
  const [ended, setEnded] = useState(isoToLocalInput(session.ended_at));
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const pins = session.pins || [];

  async function run(fn) {
    setError('');
    setSaved(false);
    try {
      onSaved(await fn());
      setSaved(true);
    } catch (err) {
      setError(err.message);
    }
  }

  const saveTimes = () => run(async () => {
    const fields = { started_at: localInputToIso(started), ended_at: localInputToIso(ended) };
    const updated = await updateSession(session.id, fields);
    return { started_at: updated.started_at, ended_at: updated.ended_at };
  });

  if (!labels) return null;
  return (
    <div style={{
      margin: '-6px 0 13px', padding: '12px 14px', display: 'grid', gap: 12,
      background: 'var(--fx-card-fill)', border: '1px solid var(--fx-hairline)', borderRadius: 14,
    }}>
      {pins.length <= 1 ? (
        <RatingSlider
          value={session.rating}
          onChange={v => run(async () => {
            await updateSession(session.id, { rating: v });
            return { rating: v, pins: pins.map(p => ({ ...p, rating: v })) };
          })}
        />
      ) : (
        pins.map(p => (
          <RatingSlider
            key={p.pin_id}
            label={`How was ${p.name}?`}
            value={p.rating}
            onChange={v => run(async () => {
              await setTripPinRating(session.id, p.pin_id, v);
              return { pins: pins.map(q => (q.pin_id === p.pin_id ? { ...q, rating: v } : q)) };
            })}
          />
        ))
      )}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <label style={pinUi.muted}>
          Started
          <input type="datetime-local" style={pinUi.input} value={started} onChange={e => setStarted(e.target.value)} />
        </label>
        <label style={pinUi.muted}>
          Ended
          <input type="datetime-local" style={pinUi.input} value={ended} onChange={e => setEnded(e.target.value)} />
        </label>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button type="button" style={pinUi.primary} onClick={saveTimes}>Save times</button>
        {saved && <span style={pinUi.muted}>Saved</span>}
      </div>
      {error && <div style={pinUi.error}>{error}</div>}
    </div>
  );
}

export default function Trips() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);

  function patchSession(id, patch) {
    setSessions(prev => prev.map(x => (x.id === id ? { ...x, ...patch } : x)));
  }

  useEffect(() => {
    getSessions()
      .then(data => {
        setSessions(Array.isArray(data) ? data : data.sessions || []);
        setLoading(false);
      })
      .catch(err => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  return (
    <div style={{
      position: 'relative',
      minHeight: '100dvh',
      background: 'radial-gradient(circle at 50% 0%, var(--fx-bg-grad-1), var(--fx-bg-grad-2) 45%, var(--fx-bg-grad-3) 100%)',
      padding: '16px 16px 100px',
      maxWidth: 480,
      margin: '0 auto',
    }}>
      <GrainOverlay />

      <h1 style={{ fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 22, color: 'var(--fx-text-primary-2)', marginBottom: 20 }}>
        Your trips
      </h1>

      {loading && (
        <div style={{ fontFamily: 'var(--fx-font-ui)', color: 'var(--fx-text-muted)', fontSize: 14 }}>Loading trips...</div>
      )}

      {error && (
        <div role="alert" style={{
          padding: '12px 14px', background: 'var(--color-rust-bg)',
          color: 'var(--color-rust)', borderRadius: 10, fontFamily: 'var(--fx-font-ui)', fontSize: 14,
        }}>
          {error.includes('404') || error.includes('failed')
            ? 'Trip history coming soon — log your first trip to get started.'
            : `Error: ${error}`}
        </div>
      )}

      {!loading && !error && sessions.length === 0 && (
        <div style={{
          padding: '24px 16px', textAlign: 'center',
          fontFamily: 'var(--fx-font-ui)', color: 'var(--fx-text-muted)', fontSize: 14,
        }}>
          No trips logged yet. Tap + to log your first catch.
        </div>
      )}

      {sessions.map((session, i) => (
        <div key={session.id ?? i}>
          <button
            type="button"
            onClick={() => setEditing(editing === session.id ? null : session.id)}
            aria-expanded={editing === session.id}
            aria-label={`Edit trip ${session.location || ''} ${session.date || ''}`}
            style={{ display: 'block', width: '100%', padding: 0, border: 'none', background: 'none', textAlign: 'left', cursor: 'pointer' }}
          >
            <TripPlate session={session} />
          </button>
          {editing === session.id && (
            <TripEditor session={session} onSaved={patch => patchSession(session.id, patch)} />
          )}
        </div>
      ))}
    </div>
  );
}
