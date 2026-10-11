import { usePinLabels, pinUi } from '../pins';

// The optional trip rating: five anchored stops, skunked … best of the year.
// value null means "not rated" and is drawn as no stop selected — never as
// the lowest one. onChange(null) is "Clear"; nothing is sent until the user
// picks a stop, so skipping is just not touching it.
export default function RatingSlider({ value, onChange, disabled = false, label = 'How was it?' }) {
  const labels = usePinLabels();
  if (!labels) return null;
  const anchors = Object.entries(labels.rating_labels)
    .map(([k, text]) => ({ value: Number(k), text }))
    .sort((a, b) => a.value - b.value);
  const min = anchors[0].value;
  const max = anchors[anchors.length - 1].value;
  const rated = value != null;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
        <span style={{ ...pinUi.muted, fontWeight: 600 }}>{label}</span>
        <span style={{ ...pinUi.muted, fontStyle: rated ? 'normal' : 'italic' }}>
          {rated ? labels.rating_labels[String(value)] : 'not rated — optional'}
          {rated && !disabled && (
            <button
              type="button"
              onClick={() => onChange(null)}
              style={{ marginLeft: 8, border: 'none', background: 'none', padding: 0, cursor: 'pointer', ...pinUi.muted, textDecoration: 'underline' }}
            >
              clear
            </button>
          )}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        // An unrated slider sits mid-track but is visibly unset (faded,
        // no label); the first touch or click sets a value.
        value={rated ? value : Math.round((min + max) / 2)}
        disabled={disabled}
        aria-label={label}
        aria-valuetext={rated ? labels.rating_labels[String(value)] : 'not rated'}
        onChange={e => onChange(Number(e.target.value))}
        onClick={e => { if (!rated) onChange(Number(e.currentTarget.value)); }}
        style={{ width: '100%', opacity: rated ? 1 : 0.35, accentColor: 'var(--fx-moss-light)' }}
      />
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 4 }}>
        {anchors.map(a => (
          <button
            key={a.value}
            type="button"
            disabled={disabled}
            onClick={() => onChange(a.value)}
            style={{
              flex: 1, border: 'none', background: 'none', padding: '2px 0', cursor: disabled ? 'default' : 'pointer',
              fontFamily: 'var(--fx-font-ui)', fontSize: 10, lineHeight: 1.2,
              color: value === a.value ? 'var(--fx-moss-lightest)' : 'var(--fx-text-muted)',
              fontWeight: value === a.value ? 600 : 400,
              textAlign: a.value === min ? 'left' : a.value === max ? 'right' : 'center',
            }}
          >
            {a.text}
          </button>
        ))}
      </div>
    </div>
  );
}
