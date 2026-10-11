import { useEffect, useState } from 'react';
import { getPinLabels } from './api';

// Every pin label and constant comes from GET /pins/labels
// (src/services/pins/labels.py) — the web app keeps no copy, so a renamed
// rating anchor or the match indicator's final name changes in one place.
let labelsPromise = null;

export function usePinLabels() {
  const [labels, setLabels] = useState(null);
  useEffect(() => {
    let live = true;
    if (!labelsPromise) {
      labelsPromise = getPinLabels().catch(err => {
        labelsPromise = null; // retry on the next mount
        throw err;
      });
    }
    labelsPromise.then(l => { if (live) setLabels(l); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return labels;
}

export function formatDistance(m) {
  if (m == null) return '';
  return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`;
}

// <input type="datetime-local"> wants local "YYYY-MM-DDTHH:mm"; the API
// stores ISO with a timezone. These convert between the two.
export function isoToLocalInput(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function localInputToIso(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export const pinUi = {
  button: {
    padding: '8px 12px', borderRadius: 8, border: '1px solid var(--fx-hairline)',
    background: 'transparent', color: 'var(--fx-text-primary)',
    fontFamily: 'var(--fx-font-ui)', fontSize: 12, fontWeight: 500, cursor: 'pointer',
  },
  primary: {
    padding: '8px 12px', borderRadius: 8, border: 'none',
    background: 'var(--fx-moss-light)', color: 'var(--fx-on-accent)',
    fontFamily: 'var(--fx-font-ui)', fontSize: 12, fontWeight: 600, cursor: 'pointer',
  },
  input: {
    width: '100%', padding: '9px 11px', borderRadius: 8, boxSizing: 'border-box',
    border: '1px solid var(--fx-hairline)', background: 'var(--fx-card-fill-quiet)',
    color: 'var(--fx-text-primary)', fontFamily: 'var(--fx-font-ui)', fontSize: 13, outline: 'none',
  },
  muted: { fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--fx-text-muted)' },
  heading: { fontFamily: 'var(--fx-font-serif)', fontSize: 16, fontWeight: 600, color: 'var(--fx-text-primary-2)' },
  tag: {
    display: 'inline-block', padding: '2px 7px', borderRadius: 6,
    background: 'var(--fx-card-fill-quiet)', border: '1px solid var(--fx-hairline)',
    fontFamily: 'var(--fx-font-ui)', fontSize: 10, fontWeight: 600,
    letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--fx-text-muted)',
  },
  error: { fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--color-rust)', marginTop: 6 },
};
