import { useState, useEffect } from 'react';
import { confirmCatchSpecies, logTrip, resolvePhotoUrl } from '../api';
import GrainOverlay from '../components/GrainOverlay';
import TripPinsCard from '../components/TripPinsCard';
import '../fishdex-tokens.css';

// A logged catch's species is a suggestion — from the text parser, and from
// a photo-vision ID if a photo was attached — never committed as fact until
// confirmed here. See the FishDex hallucination fix (species_confirmed gate
// in trip_logger.py / catches.py).
function SpeciesConfirmCard({ pending, onConfirmed }) {
  const [custom, setCustom] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const allCandidates = pending.suggested_species || [];
  // Ranked, confidence-bearing candidates (photo IDs and any specific
  // text-named species) vs. the one honest "notes didn't say" entry, which
  // is never a competing guess and is never ranked by confidence — see
  // _build_suggested_species in trip_logger.py.
  const ranked = allCandidates.filter(c => c.confidence);
  const unspecified = allCandidates.find(c => !c.confidence);

  async function handleConfirm(species) {
    const final = (species || custom).trim();
    if (!final || saving) return;
    setSaving(true);
    setError('');
    try {
      const result = await confirmCatchSpecies(pending.catch_id, final);
      onConfirmed(pending.catch_id, final, result.is_new_pb);
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return (
    <div style={{
      marginTop: 12, padding: '14px 16px',
      background: 'var(--fx-card-fill)', border: '1px solid var(--fx-hairline)',
      borderRadius: 14,
    }}>
      <div style={{ fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 15, color: 'var(--fx-text-primary-2)', marginBottom: 4 }}>
        Confirm species
      </div>
      <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--fx-text-muted)', marginBottom: 10 }}>
        {ranked.length > 1
          ? "The photo and your notes don't agree — which is it?"
          : ranked.length === 1 && unspecified
            ? 'Notes didn’t name a species — here’s what the photo suggests.'
            : 'Tap to confirm, or type the correct species below.'}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: unspecified ? 6 : 10 }}>
        {ranked.map((c, i) => (
          <button
            key={i}
            type="button"
            onClick={() => handleConfirm(c.species)}
            disabled={saving}
            style={{
              padding: '8px 14px', borderRadius: 'var(--radius-pill)',
              border: `1px solid ${i === 0 ? 'var(--fx-moss-light)' : 'var(--fx-hairline)'}`,
              background: i === 0 ? 'var(--fx-moss-light)' : 'transparent',
              color: i === 0 ? 'var(--fx-on-accent)' : 'var(--fx-text-primary)',
              fontFamily: 'var(--fx-font-ui)', fontSize: 13,
              cursor: saving ? 'default' : 'pointer',
              display: 'flex', alignItems: 'center', gap: 6, textTransform: 'capitalize',
            }}
          >
            {c.species}
            <span style={{ fontSize: 10, color: i === 0 ? 'var(--fx-on-accent)' : 'var(--fx-text-muted)' }}>
              {c.source === 'photo' ? `📷 ${c.confidence}` : c.confidence}
            </span>
          </button>
        ))}
      </div>
      {unspecified && (
        <button
          type="button"
          onClick={() => handleConfirm(unspecified.species)}
          disabled={saving}
          style={{
            display: 'block', border: 'none', background: 'none', padding: '2px 0 10px',
            color: 'var(--fx-text-muted)', fontFamily: 'var(--fx-font-serif)', fontStyle: 'italic', fontSize: 12,
            cursor: saving ? 'default' : 'pointer', textAlign: 'left',
          }}
        >
          Notes {unspecified.note} — log as "{unspecified.species}" instead
        </button>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          type="text"
          value={custom}
          onChange={e => setCustom(e.target.value)}
          placeholder="Or type the correct species"
          style={{
            flex: 1, padding: '10px 12px', borderRadius: 8,
            border: '1px solid var(--fx-hairline)', background: 'var(--fx-card-fill-quiet)',
            color: 'var(--fx-text-primary)', fontFamily: 'var(--fx-font-ui)', fontSize: 13, outline: 'none',
          }}
        />
        <button
          type="button"
          onClick={() => handleConfirm(custom)}
          disabled={saving || !custom.trim()}
          style={{
            padding: '10px 16px', borderRadius: 8, border: 'none',
            background: custom.trim() ? 'var(--fx-moss-light)' : 'var(--fx-hairline)',
            color: custom.trim() ? 'var(--fx-on-accent)' : 'var(--fx-text-muted)',
            fontFamily: 'var(--fx-font-ui)', fontSize: 13, fontWeight: 600,
            cursor: custom.trim() ? 'pointer' : 'default',
          }}
        >
          Use this
        </button>
      </div>
      {error && (
        <div style={{ marginTop: 8, fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--color-rust)' }}>{error}</div>
      )}
    </div>
  );
}

function formatDuration(seconds) {
  if (seconds == null) return null;
  const mins = Math.round(seconds / 60);
  if (mins < 1) return 'under a minute';
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return remMins ? `${hours}h ${remMins}m` : `${hours}h`;
}

function StatCell({ label, value }) {
  return (
    <div>
      <div style={{ fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 22, color: 'var(--fx-text-primary-2)' }}>
        {value}
      </div>
      <div style={{
        fontFamily: 'var(--fx-font-ui)', fontSize: 10, fontWeight: 600,
        letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--fx-text-muted)', marginTop: 2,
      }}>
        {label}
      </div>
    </div>
  );
}

// The "obscured" privacy tier by default: display-level location name only
// (e.g. "Sixteen Mile Creek", already resolved server-side the same way
// every other screen shows it), never raw lat/lng — this card has no share/
// export action yet, but the boundary is set now so nothing needs
// retrofitting when one exists. See PRODUCT.md privacy rule.
//
// The end-of-session "Strava moment" — the new success state in place of a
// plain "Trip logged" box. Reads entirely from data the session already
// produced: summary.catches is the /log-trip(/photo) response's per-catch
// summary (species/count/size/confirmed/PB, confirmed rows and
// still-pending ones alike), kept in sync as SpeciesConfirmCard entries
// below get confirmed (see LogTrip's handleCatchConfirmed) — so the species
// count, biggest-catch label, and PB badge all update live rather than
// freezing at whatever was knowable the instant "End session" returned.
function SessionSummaryCard({ summary }) {
  // "unidentified sp." is a real logged catch but not a real species ID —
  // excluded so the species count reads as a genuine number, not inflated
  // by an anonymous-fish bucket (explicit product decision, not a default).
  const speciesCount = new Set(
    summary.catches
      .filter(c => c.species_confirmed && (c.species || '').trim().toLowerCase() !== 'unidentified sp.')
      .map(c => c.species.trim().toLowerCase())
  ).size;

  const sized = summary.catches.filter(c => c.biggest_size_cm != null);
  const biggest = sized.length
    ? sized.reduce((a, b) => (b.biggest_size_cm > a.biggest_size_cm ? b : a))
    : null;

  const hasNewPb = summary.catches.some(c => c.is_new_pb);
  const durationLabel = formatDuration(summary.durationSeconds);
  const photoUrl = biggest?.photo_url ? resolvePhotoUrl(biggest.photo_url) : null;

  const stats = [
    { label: 'Fish', value: summary.totalFish },
    { label: 'Species', value: speciesCount },
  ];
  if (durationLabel) stats.push({ label: 'Duration', value: durationLabel });

  const pbBadge = (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 5,
      padding: '5px 10px', borderRadius: 14,
      background: 'rgba(24,18,8,.65)', backdropFilter: 'blur(4px)',
      border: '1px solid var(--fx-brass)',
    }}>
      <span aria-hidden="true" style={{ color: 'var(--fx-brass-star)' }}>★</span>
      <span style={{
        fontFamily: 'var(--fx-font-ui)', fontSize: 10, fontWeight: 600,
        letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--fx-brass-light)',
      }}>
        New Personal Best
      </span>
    </div>
  );

  return (
    <div style={{
      marginTop: 12, borderRadius: 18, overflow: 'hidden',
      border: `1px solid ${hasNewPb ? 'var(--fx-brass)' : 'var(--fx-hairline)'}`,
      background: 'var(--fx-card-fill)',
    }}>
      {photoUrl && (
        <div style={{ position: 'relative', height: 180, overflow: 'hidden' }}>
          <img src={photoUrl} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
          <div style={{
            position: 'absolute', inset: 0,
            background: 'linear-gradient(transparent 40%, rgba(11,12,8,.6) 70%, rgba(9,10,6,.92) 100%)',
          }} />
          {hasNewPb && (
            <div style={{ position: 'absolute', top: 12, right: 12 }}>{pbBadge}</div>
          )}
          <div style={{ position: 'absolute', left: 16, right: 16, bottom: 12 }}>
            <div style={{
              fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 20,
              color: 'var(--fx-text-primary-2)', textTransform: 'capitalize',
            }}>
              {biggest.species}
            </div>
            <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--fx-brass-light)' }}>
              biggest catch · {(biggest.biggest_size_cm / 2.54).toFixed(1)}″
              {!biggest.species_confirmed && ' · confirm below'}
            </div>
          </div>
        </div>
      )}

      <div style={{ padding: '16px 18px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span aria-hidden="true" style={{ color: 'var(--fx-moss-lightest)', fontSize: 14 }}>❦</span>
            <span style={{
              fontFamily: 'var(--fx-font-ui)', fontSize: 10, fontWeight: 600,
              letterSpacing: '.08em', textTransform: 'uppercase', color: 'var(--fx-moss-lightest)',
            }}>
              Session Complete
            </span>
          </div>
          {hasNewPb && !photoUrl && pbBadge}
        </div>

        <div style={{
          display: 'grid', gridTemplateColumns: `repeat(${stats.length}, 1fr)`, gap: 12,
          marginBottom: (summary.locationName || summary.conditions) ? 14 : 0,
        }}>
          {stats.map(s => <StatCell key={s.label} label={s.label} value={s.value} />)}
        </div>

        {(summary.locationName || summary.conditions) && (
          <div style={{
            fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--fx-text-muted)',
            display: 'flex', flexWrap: 'wrap', gap: 12,
          }}>
            {summary.locationName && <span>📍 {summary.locationName}</span>}
            {summary.conditions?.air_temp_c != null && <span>{Math.round(summary.conditions.air_temp_c)}°C</span>}
            {summary.conditions?.pressure_hpa != null && <span>{Math.round(summary.conditions.pressure_hpa)} hPa</span>}
          </div>
        )}
      </div>
    </div>
  );
}

async function extractExif(file) {
  return new Promise(resolve => {
    const reader = new FileReader();
    reader.onload = e => {
      try {
        const view = new DataView(e.target.result);
        if (view.getUint16(0) !== 0xFFD8) { resolve(null); return; }
        let offset = 2;
        while (offset < view.byteLength - 2) {
          const marker = view.getUint16(offset);
          if (marker === 0xFFE1) {
            const exifData = parseExifData(view, offset + 4);
            resolve(exifData); return;
          }
          if ((marker & 0xFF00) !== 0xFF00) break;
          offset += 2 + view.getUint16(offset + 2);
        }
        resolve(null);
      } catch { resolve(null); }
    };
    reader.readAsArrayBuffer(file);
  });
}

function parseExifData(view, start) {
  try {
    const header = String.fromCharCode(
      view.getUint8(start), view.getUint8(start+1),
      view.getUint8(start+2), view.getUint8(start+3)
    );
    if (header !== 'Exif') return null;
    const tiffStart = start + 6;
    const littleEndian = view.getUint16(tiffStart) === 0x4949;
    const getUint16 = o => view.getUint16(tiffStart + o, littleEndian);
    const getUint32 = o => view.getUint32(tiffStart + o, littleEndian);
    const ifdOffset = getUint32(4);
    const numEntries = getUint16(ifdOffset);
    let gpsIfdOffset = null, dateTimeStr = null;
    for (let i = 0; i < numEntries; i++) {
      const e = ifdOffset + 2 + i * 12;
      const tag = getUint16(e);
      if (tag === 0x8825) gpsIfdOffset = getUint32(e + 8);
      if (tag === 0x9003 || tag === 0x0132) {
        const count = getUint32(e + 4);
        const valOffset = getUint32(e + 8);
        let str = '';
        for (let j = 0; j < count - 1; j++)
          str += String.fromCharCode(view.getUint8(tiffStart + valOffset + j));
        dateTimeStr = str;
      }
    }
    let lat = null, lng = null;
    if (gpsIfdOffset !== null) {
      const gpsEntries = getUint16(gpsIfdOffset);
      let latRef = 'N', lngRef = 'E', latRaw = null, lngRaw = null;
      for (let i = 0; i < gpsEntries; i++) {
        const e = gpsIfdOffset + 2 + i * 12;
        const tag = getUint16(e);
        if (tag === 1) latRef = String.fromCharCode(view.getUint8(tiffStart + getUint32(e+8)));
        if (tag === 3) lngRef = String.fromCharCode(view.getUint8(tiffStart + getUint32(e+8)));
        if (tag === 2) latRaw = readGpsCoord(view, tiffStart, getUint32(e+8), littleEndian);
        if (tag === 4) lngRaw = readGpsCoord(view, tiffStart, getUint32(e+8), littleEndian);
      }
      if (latRaw !== null && lngRaw !== null) {
        lat = latRaw * (latRef === 'S' ? -1 : 1);
        lng = lngRaw * (lngRef === 'W' ? -1 : 1);
      }
    }
    let isoDate = null;
    if (dateTimeStr) {
      const parts = dateTimeStr.match(/(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})/);
      if (parts) isoDate = `${parts[1]}-${parts[2]}-${parts[3]}T${parts[4]}:${parts[5]}:${parts[6]}`;
    }
    return { lat, lng, takenAt: isoDate };
  } catch { return null; }
}

function readGpsCoord(view, tiffStart, offset, le) {
  const d = view.getUint32(tiffStart+offset,le)/view.getUint32(tiffStart+offset+4,le);
  const m = view.getUint32(tiffStart+offset+8,le)/view.getUint32(tiffStart+offset+12,le);
  const s = view.getUint32(tiffStart+offset+16,le)/view.getUint32(tiffStart+offset+20,le);
  return d + m/60 + s/3600;
}

const SIZE_UNIT_STORAGE_KEY = 'fishdex_size_unit';

// Module-scoped id counter for React keys on catch entries — not persisted,
// just needs to be unique within a session's lifetime.
let _catchIdSeq = 0;
function makeBlankCatch() {
  _catchIdSeq += 1;
  return { id: _catchIdSeq, kind: 'detailed', photoFile: null, preview: null, species: '', count: 1, biggestSize: '', bait: '' };
}

// A fast-tally "+1 fish" entry — same field shape as a detailed catch (so
// composeSessionText/updateCatch/the submit payload never need to special-
// case it), plus caughtAt: the actual tap time, since every catch in a
// session is inserted server-side together at submit time (see caught_at
// in trip_logger.py).
function makeTallyCatch() {
  _catchIdSeq += 1;
  return {
    id: _catchIdSeq, kind: 'tally', photoFile: null, preview: null,
    species: '', count: 1, biggestSize: '', bait: '',
    caughtAt: new Date().toISOString(),
  };
}

const fieldInputStyle = {
  width: '100%',
  padding: '10px 12px',
  borderRadius: 8,
  border: '1px solid var(--fx-hairline)',
  background: 'var(--fx-card-fill-quiet)',
  color: 'var(--fx-text-primary)',
  fontFamily: 'var(--fx-font-ui)',
  fontSize: 14,
  outline: 'none',
  boxSizing: 'border-box',
};

// Composes the structured session into the single freeform string the
// backend's NL parser (parse_session_from_text) actually understands — see
// the TODO(backend) note in api.js. This keeps species extraction / photo
// vision / pending-catch confirmation working exactly as it does today;
// count, size, and bait ride along in the sentence for the parser to see,
// but are NOT guaranteed to land in structured columns until the backend
// reads catches_json.
function composeSessionText(sessionNotes, catches, unit) {
  const lines = [];
  const notes = sessionNotes.trim();
  if (notes) lines.push(notes);

  for (const c of catches) {
    const species = c.species.trim();
    if (!species) continue;
    const count = c.count || 1;
    let sentence = `Caught ${count} ${species}`;
    if (c.bait.trim()) sentence += ` on ${c.bait.trim()}`;
    if (c.biggestSize.trim()) sentence += `, biggest ${c.biggestSize.trim()}${unit}`;
    lines.push(sentence + '.');
  }

  if (lines.length === 0) lines.push('Fishing trip.');
  return lines.join(' ');
}

function CountStepper({ value, onChange }) {
  const stepperBtnStyle = {
    width: 44, height: 44, borderRadius: '50%', flexShrink: 0,
    border: '1px solid var(--fx-hairline)', background: 'var(--fx-card-fill-quiet)',
    color: 'var(--fx-moss-lightest)', fontSize: 20, fontWeight: 600, lineHeight: 1,
    display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
  };
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <button
        type="button"
        onClick={() => onChange(Math.max(1, value - 1))}
        aria-label="Decrease count"
        style={stepperBtnStyle}
      >−</button>
      <div style={{
        minWidth: 22, textAlign: 'center',
        fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 18, color: 'var(--fx-text-primary-2)',
      }}>{value}</div>
      <button
        type="button"
        onClick={() => onChange(value + 1)}
        aria-label="Increase count"
        style={stepperBtnStyle}
      >+</button>
    </div>
  );
}

function UnitToggle({ unit, onChange }) {
  return (
    <div style={{
      display: 'flex', background: 'var(--fx-card-fill-quiet)',
      border: '1px solid var(--fx-hairline)', borderRadius: 8, padding: 2, gap: 2, flexShrink: 0,
    }}>
      {['in', 'cm'].map(u => (
        <button
          key={u}
          type="button"
          onClick={() => onChange(u)}
          style={{
            padding: '6px 10px', borderRadius: 6, border: 'none',
            background: unit === u ? 'var(--fx-moss-light)' : 'transparent',
            color: unit === u ? 'var(--fx-on-accent)' : 'var(--fx-text-muted)',
            fontFamily: 'var(--fx-font-ui)', fontSize: 11, fontWeight: 600, cursor: 'pointer',
          }}
        >{u}</button>
      ))}
    </div>
  );
}

// One "page" of the field notebook per catch — subtle fill + radius for
// separation rather than a hard-bordered form row, per the naturalist
// field-journal style used elsewhere (FishDex plates, Map's bottom sheet).
function CatchEntryCard({ catchEntry, index, canRemove, unit, onChange, onRemove, onPhoto }) {
  const fileId = `catch-photo-${catchEntry.id}`;
  const speciesId = `catch-species-${catchEntry.id}`;

  return (
    <div style={{
      background: 'var(--fx-card-fill)', borderRadius: 14,
      padding: '14px 14px 16px', marginBottom: 14,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{
          fontFamily: 'var(--fx-font-ui)', fontSize: 10, fontWeight: 700,
          letterSpacing: '.12em', textTransform: 'uppercase', color: 'var(--fx-moss-light)',
        }}>
          Catch {index + 1}
        </span>
        {canRemove && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={`Remove catch ${index + 1}`}
            style={{
              width: 32, height: 32, borderRadius: '50%', border: 'none', background: 'none',
              color: 'var(--fx-text-muted)', fontSize: 18, cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          ><span aria-hidden="true">×</span></button>
        )}
      </div>

      <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
        <label
          htmlFor={fileId}
          className="photo-dropzone"
          style={{
            flexShrink: 0, width: 72, height: 72, borderRadius: 10, overflow: 'hidden', cursor: 'pointer',
            border: catchEntry.preview ? 'none' : '1.5px dashed var(--fx-dashed-border)',
            background: catchEntry.preview ? 'transparent' : 'var(--fx-card-fill-quiet)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <input
            id={fileId}
            type="file"
            accept="image/*"
            className="sr-only"
            onChange={e => onPhoto(e.target.files[0])}
          />
          {catchEntry.preview ? (
            <img src={catchEntry.preview} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          ) : (
            <span aria-hidden="true" style={{ fontSize: 20, color: 'var(--fx-text-locked-3)' }}>📷</span>
          )}
        </label>

        <div style={{ flex: 1, minWidth: 0 }}>
          <label htmlFor={speciesId} className="sr-only">Species</label>
          <input
            id={speciesId}
            type="text"
            value={catchEntry.species}
            onChange={e => onChange({ species: e.target.value })}
            placeholder="Species (e.g. smallmouth bass)"
            style={fieldInputStyle}
          />
          <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 11, color: 'var(--fx-text-muted)' }}>Count</span>
            <CountStepper value={catchEntry.count} onChange={v => onChange({ count: v })} />
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
        <label htmlFor={`size-${catchEntry.id}`} className="sr-only">Biggest size ({unit})</label>
        <input
          id={`size-${catchEntry.id}`}
          type="number"
          inputMode="decimal"
          min="0"
          step="0.1"
          value={catchEntry.biggestSize}
          onChange={e => onChange({ biggestSize: e.target.value })}
          placeholder={`Biggest size (${unit})`}
          style={{ ...fieldInputStyle, flex: 1 }}
        />
      </div>

      <label htmlFor={`bait-${catchEntry.id}`} className="sr-only">Bait or lure</label>
      <input
        id={`bait-${catchEntry.id}`}
        type="text"
        value={catchEntry.bait}
        onChange={e => onChange({ bait: e.target.value })}
        placeholder="Bait or lure"
        style={fieldInputStyle}
      />
    </div>
  );
}

function relativeTimeLabel(isoString, nowMs) {
  const then = new Date(isoString).getTime();
  const seconds = Math.max(0, Math.round((nowMs - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return `${hours}h ago`;
}

// The fast-tally row: one tap already recorded this catch (see addTally in
// LogTrip below) — this just displays it and offers an optional, always-
// visible (never a popup requiring a dismiss tap) species tag. Deliberately
// a separate, smaller component from CatchEntryCard rather than a kind-
// branch inside it: a tally entry has none of CatchEntryCard's photo
// dropzone / count stepper / size field, so branching inside it would mean
// carrying a second, mostly-empty rendering path in one component.
function TallyRow({ catchEntry, nowTick, onChange, onRemove }) {
  const speciesId = `tally-species-${catchEntry.id}`;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      background: 'var(--fx-card-fill)', borderRadius: 12,
      padding: '10px 12px', marginBottom: 10,
    }}>
      <span aria-hidden="true" style={{
        flexShrink: 0, width: 28, height: 28, borderRadius: '50%',
        background: 'var(--fx-moss-light)', color: 'var(--fx-on-accent)',
        fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 13,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>1</span>

      <label htmlFor={speciesId} className="sr-only">Species (optional)</label>
      <input
        id={speciesId}
        type="text"
        value={catchEntry.species}
        onChange={e => onChange({ species: e.target.value })}
        placeholder="Tag species (optional)"
        style={{
          flex: 1, minWidth: 0, padding: '8px 10px', borderRadius: 8,
          border: '1px solid var(--fx-hairline)', background: 'var(--fx-card-fill-quiet)',
          color: 'var(--fx-text-primary)', fontFamily: 'var(--fx-font-ui)', fontSize: 13, outline: 'none',
        }}
      />

      <span style={{
        flexShrink: 0, fontFamily: 'var(--fx-font-ui)', fontSize: 11, color: 'var(--fx-text-muted)',
      }}>
        {relativeTimeLabel(catchEntry.caughtAt, nowTick)}
      </span>

      <button
        type="button"
        onClick={onRemove}
        aria-label="Remove this catch"
        style={{
          flexShrink: 0, width: 28, height: 28, borderRadius: '50%', border: 'none', background: 'none',
          color: 'var(--fx-text-muted)', fontSize: 16, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}
      ><span aria-hidden="true">×</span></button>
    </div>
  );
}

export default function LogTrip({ onNavigate }) {
  const [sessionNotes, setSessionNotes] = useState('');
  const [exifData, setExifData] = useState(null);
  const [locationAttempted, setLocationAttempted] = useState(false);
  const [unit, setUnit] = useState(() => localStorage.getItem(SIZE_UNIT_STORAGE_KEY) || 'in');
  const [catches, setCatches] = useState(() => [makeBlankCatch()]);
  const [status, setStatus] = useState(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [pendingCatches, setPendingCatches] = useState([]);
  // The end-of-session summary card's data — set from the log-trip response
  // on submit, updated in place as SpeciesConfirmCard entries below resolve
  // (see handleCatchConfirmed). null until the first successful submit.
  const [sessionSummary, setSessionSummary] = useState(null);

  // Live session state — purely client-side for this phase, same as the
  // rest of this form: nothing persists until "Log trip"/"End session" is
  // submitted. If sessionMode never leaves 'idle', none of this renders and
  // the screen behaves exactly as it did before this feature.
  const [sessionMode, setSessionMode] = useState('idle'); // 'idle' | 'live'
  const [sessionStartedAt, setSessionStartedAt] = useState(null);
  const [nowTick, setNowTick] = useState(() => Date.now());

  useEffect(() => {
    localStorage.setItem(SIZE_UNIT_STORAGE_KEY, unit);
  }, [unit]);

  // One shared clock for the whole live session — drives both the elapsed-
  // time header and every TallyRow's relative timestamp, so no per-row
  // intervals are needed.
  useEffect(() => {
    if (sessionMode !== 'live') return;
    const id = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, [sessionMode]);

  function updateCatch(id, patch) {
    setCatches(prev => prev.map(c => (c.id === id ? { ...c, ...patch } : c)));
  }

  function addCatch() {
    setCatches(prev => [...prev, makeBlankCatch()]);
  }

  function removeCatch(id) {
    setCatches(prev => (prev.length <= 1 ? prev : prev.filter(c => c.id !== id)));
  }

  // Location is captured once per session — shared by both entry points:
  // "Start fishing" (no photo yet, straight to device geolocation) and
  // adding the first photo to any catch entry (EXIF first, geolocation
  // fallback). Callers are responsible for the locationAttempted guard so
  // this stays a plain "go get a location" call either way.
  async function captureLocationOnce(file = null) {
    if (file) {
      const exif = await extractExif(file);
      if (exif && exif.lat) {
        setExifData(exif);
        return;
      }
    }
    setExifData({ lat: null, lng: null, takenAt: null, trying: true });
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        pos => setExifData({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          takenAt: new Date().toISOString(),
        }),
        () => setExifData(null),
        { timeout: 8000 }
      );
    } else {
      setExifData(null);
    }
  }

  async function handleCatchPhoto(catchId, file) {
    if (!file) return;
    updateCatch(catchId, { photoFile: file, preview: URL.createObjectURL(file) });

    if (locationAttempted) return;
    setLocationAttempted(true);
    // Note: if a live session already captured device geolocation via
    // "Start fishing", locationAttempted is already true by the time any
    // photo is added, so this photo's (likely more precise) embedded EXIF
    // GPS is never consulted — same "captured once per session" invariant
    // as idle mode, just resolved in favor of whichever source ran first.
    captureLocationOnce(file);
  }

  async function startFishing() {
    if (!locationAttempted) {
      setLocationAttempted(true);
      captureLocationOnce(); // no file yet — goes straight to device geolocation
    }
    setSessionMode('live');
    setSessionStartedAt(Date.now());
    // Clear the previous session's recap — it's shown until a new session
    // starts, not just until the next tap, so a solo "log a catch" isn't
    // stuck staring at last time's card the moment fishing resumes.
    setSessionSummary(null);
  }

  function addTally() {
    setCatches(prev => [...prev, makeTallyCatch()]);
  }

  // Undoes only what "Start fishing" itself introduced — tally taps and the
  // live-mode header — leaving any detailed-card content already typed
  // untouched. No network call. Exists because "End session" reuses the
  // same hasContent gate as "Log trip": without this, a session with zero
  // catches has no way back to idle mode short of a page refresh.
  function cancelSession() {
    setCatches(prev => prev.filter(c => c.kind !== 'tally'));
    setSessionMode('idle');
    setSessionStartedAt(null);
  }

  async function handleSubmit() {
    if (status === 'loading') return;
    setStatus('loading');
    try {
      const text = composeSessionText(sessionNotes, catches, unit);
      // Backend accepts exactly one photo per submission today — see the
      // TODO(backend) note in api.js. First catch (in entry order) that has
      // a photo wins; any others are still captured in the UI and sent in
      // catches_json, but won't be uploaded until the backend supports it.
      const photoFile = catches.find(c => c.photoFile)?.photoFile || null;
      // A fast-tally entry (kind: 'tally') is always sent even with no
      // species and no photo — that's the whole point of "+1 fish": a bare
      // tap is a complete, valid entry (see log_session's fast_tally branch
      // in trip_logger.py). Detailed entries keep the original rule: only
      // sent once they have a species or a photo.
      const catchesPayload = catches
        .filter(c => c.kind === 'tally' || c.species.trim() || c.photoFile)
        .map(c => ({
          species: c.species.trim() || null,
          count: c.count,
          biggest_size: c.biggestSize.trim() ? `${c.biggestSize.trim()}${unit}` : null,
          bait: c.bait.trim() || null,
          has_photo: !!c.photoFile,
          source: c.kind === 'tally' ? 'fast_tally' : 'detailed',
          caught_at: c.caughtAt || null,
        }));

      // Snapshot before the post-submit reset below clears it — duration is
      // computed purely client-side (both endpoints of it, the "Start
      // fishing" tap and this very moment, already live in this component;
      // no backend round-trip needed). null (old-style single-catch log
      // with no live session) means the summary card omits the stat.
      const endedAt = Date.now();
      const durationSeconds = sessionStartedAt ? (endedAt - sessionStartedAt) / 1000 : null;

      // The timer's two ends are saved with the trip (editable later from
      // Trips) so fish per hour can be worked out for this trip.
      const result = await logTrip(
        text, exifData?.lat, exifData?.lng, exifData?.takenAt, photoFile, catchesPayload,
        sessionStartedAt ? new Date(sessionStartedAt).toISOString() : null,
        sessionStartedAt ? new Date(endedAt).toISOString() : null,
      );
      setStatus('success');
      setPendingCatches(result.pending_catches || []);
      setSessionSummary({
        // Server truth, not the client's own filtered catchesPayload — this
        // is what actually got persisted, so it can't drift from a branch
        // that silently drops an entry server-side.
        // A catch with no count is stored as null and counted as one fish
        // here, where fish are added up.
        totalFish: (result.catches || []).reduce((sum, c) => sum + (c.count ?? 1), 0),
        durationSeconds,
        locationName: result.location_name || null,
        conditions: result.conditions || null,
        catches: result.catches || [],
        sessionId: result.session_id,
        pinLinks: result.pin_links || [],
      });
      setSessionNotes('');
      setExifData(null);
      setLocationAttempted(false);
      setCatches([makeBlankCatch()]);
      setSessionMode('idle');
      setSessionStartedAt(null);
    } catch (err) {
      setStatus('error');
      setErrorMsg(err.message);
    }
  }

  function handleCatchConfirmed(catchId, confirmedSpecies, isNewPb) {
    setPendingCatches(prev => prev.filter(p => p.catch_id !== catchId));
    // Keeps the summary card's species count / biggest-catch label / PB
    // badge live as confirmations land, instead of freezing at whatever
    // was knowable the instant "End session" returned.
    setSessionSummary(prev => prev && {
      ...prev,
      catches: prev.catches.map(c =>
        c.catch_id === catchId
          ? { ...c, species: confirmedSpecies, species_confirmed: true, is_new_pb: isNewPb }
          : c
      ),
    });
  }

  const hasGps = exifData && exifData.lat && !exifData.trying;
  const tryingGps = exifData?.trying;
  const hasContent = sessionNotes.trim() || catches.some(c => c.kind === 'tally' || c.species.trim() || c.photoFile);

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
      <h1 style={{ fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 22, color: 'var(--fx-text-primary-2)', marginBottom: 4 }}>
        Log a trip
      </h1>
      <p style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--fx-text-muted)', marginBottom: 16 }}>
        First photo sets your location. Log every fish you caught below.
      </p>

      {sessionMode === 'idle' && (
        <button
          type="button"
          onClick={startFishing}
          style={{
            width: '100%', padding: '12px', marginBottom: 16,
            background: 'var(--fx-card-fill)', border: '1px solid var(--fx-moss-light)', borderRadius: 'var(--radius-pill)',
            color: 'var(--fx-moss-lightest)', fontFamily: 'var(--fx-font-ui)', fontSize: 14, fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          <span aria-hidden="true">🎣</span> Start fishing
        </button>
      )}

      <label htmlFor="session-notes" style={{
        display: 'block', fontFamily: 'var(--fx-font-serif)', fontSize: 13, fontWeight: 600,
        color: 'var(--fx-text-primary-2)', marginBottom: 6,
      }}>
        Session notes
      </label>
      <textarea
        id="session-notes"
        value={sessionNotes}
        onChange={e => setSessionNotes(e.target.value)}
        placeholder="Water conditions, weather, access point — optional"
        style={{
          width: '100%',
          background: 'var(--fx-card-fill)',
          border: '1px solid var(--fx-hairline)',
          borderRadius: 12,
          padding: '12px 14px',
          color: 'var(--fx-text-primary)',
          fontFamily: 'var(--fx-font-ui)',
          fontSize: 14,
          resize: 'none',
          outline: 'none',
          minHeight: 76,
          lineHeight: 1.5,
          boxSizing: 'border-box',
        }}
      />

      {locationAttempted && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6, marginTop: 8,
          fontFamily: 'var(--fx-font-ui)', fontSize: 12, fontWeight: 600,
        }}>
          {hasGps && (
            <span style={{ color: 'var(--fx-moss-lightest)' }}>
              <span aria-hidden="true">📍</span> GPS captured
              {exifData.takenAt ? ` · ${new Date(exifData.takenAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
            </span>
          )}
          {tryingGps && (
            <span style={{ color: 'var(--fx-text-primary-2)', fontWeight: 400 }}>
              <span aria-hidden="true">📍</span> Getting location...
            </span>
          )}
          {!hasGps && !tryingGps && (
            <span style={{ color: 'var(--color-rust)' }}>
              <span aria-hidden="true">📍</span> No GPS — describe location in session notes
            </span>
          )}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '22px 0 10px' }}>
        <span style={{
          fontFamily: 'var(--fx-font-ui)', fontSize: 11, fontWeight: 700,
          letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--fx-text-muted)',
        }}>
          Catches
        </span>
        <UnitToggle unit={unit} onChange={setUnit} />
      </div>

      {sessionMode === 'live' && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14,
        }}>
          <span style={{
            fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--fx-text-muted)', flexShrink: 0,
          }}>
            Fishing for {relativeTimeLabel(new Date(sessionStartedAt).toISOString(), nowTick).replace(' ago', '')}
          </span>
          <button
            type="button"
            onClick={addTally}
            style={{
              flex: 1, padding: '12px', border: 'none', borderRadius: 'var(--radius-pill)',
              background: 'var(--fx-moss-light)', color: 'var(--fx-on-accent)',
              fontFamily: 'var(--fx-font-ui)', fontSize: 14, fontWeight: 700,
              cursor: 'pointer',
            }}
          >
            + 1 fish
          </button>
          <button
            type="button"
            onClick={cancelSession}
            style={{
              flexShrink: 0, padding: '10px 12px', border: 'none', background: 'none',
              color: 'var(--fx-text-muted)', fontFamily: 'var(--fx-font-ui)', fontSize: 12,
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
        </div>
      )}

      {catches.map((c, i) =>
        c.kind === 'tally' ? (
          <TallyRow
            key={c.id}
            catchEntry={c}
            nowTick={nowTick}
            onChange={patch => updateCatch(c.id, patch)}
            onRemove={() => removeCatch(c.id)}
          />
        ) : (
          <CatchEntryCard
            key={c.id}
            catchEntry={c}
            index={i}
            canRemove={catches.length > 1}
            unit={unit}
            onChange={patch => updateCatch(c.id, patch)}
            onRemove={() => removeCatch(c.id)}
            onPhoto={file => handleCatchPhoto(c.id, file)}
          />
        )
      )}

      <button
        type="button"
        onClick={addCatch}
        style={{
          width: '100%', padding: '12px', marginBottom: 20,
          background: 'transparent', border: '1px dashed var(--fx-dashed-border)', borderRadius: 'var(--radius-pill)',
          color: 'var(--fx-moss-light)', fontFamily: 'var(--fx-font-ui)', fontSize: 13, fontWeight: 600,
          cursor: 'pointer',
        }}
      >
        + Add another catch
      </button>

      <button
        onClick={handleSubmit}
        disabled={!hasContent || status === 'loading'}
        style={{
          width: '100%',
          background: hasContent && status !== 'loading' ? 'var(--fx-moss-light)' : 'var(--fx-hairline)',
          color: hasContent && status !== 'loading' ? 'var(--fx-on-accent)' : 'var(--fx-text-muted)',
          border: 'none',
          borderRadius: 'var(--radius-pill)',
          padding: '14px',
          fontFamily: 'var(--fx-font-ui)',
          fontSize: 15,
          fontWeight: 600,
          cursor: hasContent && status !== 'loading' ? 'pointer' : 'default',
          transition: 'background 0.15s',
        }}
      >
        {status === 'loading' ? 'Logging...' : sessionMode === 'live' ? 'End session' : 'Log trip'}
      </button>

      {/* Honest-gap notice, not a silent drop. Species, count, size, and bait
          are all genuinely persisted per catch today (see trip_logger.py's
          structured_catches path) — only multi-photo upload is still a real
          gap: only the first catch's photo is sent (see handleSubmit above
          and the TODO(backend) note in api.js). */}
      <p style={{
        marginTop: 8, fontFamily: 'var(--fx-font-ui)', fontSize: 11, color: 'var(--fx-text-dim)',
        lineHeight: 1.5, textAlign: 'center',
      }}>
        Only the first catch's photo is uploaded today — add more here and
        they'll be captured, but not sent until multi-photo upload ships.
      </p>

      {status === 'success' && (
        <>
          {sessionSummary && <SessionSummaryCard summary={sessionSummary} />}
          {sessionSummary && (
            <TripPinsCard
              key={sessionSummary.sessionId}
              sessionId={sessionSummary.sessionId}
              initialLinks={sessionSummary.pinLinks}
            />
          )}
          {pendingCatches.map(p => (
            <SpeciesConfirmCard key={p.catch_id} pending={p} onConfirmed={handleCatchConfirmed} />
          ))}
        </>
      )}
      {status === 'error' && (
        <div role="alert" style={{
          marginTop: 12, padding: '12px 14px',
          background: 'var(--color-rust-bg)', color: 'var(--color-rust)',
          borderRadius: 10, fontFamily: 'var(--fx-font-ui)', fontSize: 14,
        }}>
          Error: {errorMsg}
        </div>
      )}
    </div>
  );
}
