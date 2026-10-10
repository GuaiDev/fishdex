// Details sheet for one tapped anchor on the Explore map. Like the Map
// screen's other sheet it is a lightweight panel, not a modal: no focus trap,
// background map stays interactive.
import {
  anchorStyle, anchorTypeLabel, anchorSubtypeLabel, anchorSourceLabel, anchorTitle, anchorSvg,
} from './anchors';

export default function AnchorSheet({ anchor, stretchName, onClose }) {
  const subtype = anchorSubtypeLabel(anchor.anchor_type, anchor.subtype);
  const { label } = anchorStyle(anchor.anchor_type);
  return (
    <div role="dialog" aria-label="Anchor details" style={{
      position: 'fixed', bottom: 64, left: 0, right: 0, maxWidth: 480, margin: '0 auto',
      background: 'var(--fx-card-fill)', borderTop: '1px solid var(--fx-hairline)',
      borderRadius: '16px 16px 0 0', padding: '16px 16px 24px', zIndex: 2000,
      maxHeight: '55dvh', overflowY: 'auto',
    }}>
      <button
        onClick={onClose}
        aria-label="Close"
        style={{
          position: 'absolute', top: 4, right: 8, width: 44, height: 44,
          background: 'none', border: 'none', color: 'var(--fx-text-muted)', fontSize: 20, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}
      ><span aria-hidden="true">×</span></button>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, paddingRight: 44 }}>
        <span aria-hidden="true" style={{ display: 'inline-flex', flexShrink: 0 }}
          dangerouslySetInnerHTML={{ __html: anchorSvg(anchor.anchor_type) }} />
        <div style={{ fontFamily: 'var(--fx-font-serif)', fontSize: 16, fontWeight: 600, color: 'var(--fx-text-primary-2)' }}>
          {anchorTitle(anchor)}
        </div>
      </div>

      <dl style={{
        margin: '8px 0 12px', display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 12px',
        fontFamily: 'var(--fx-font-ui)', fontSize: 12,
      }}>
        <dt style={{ color: 'var(--fx-text-muted)' }}>Type</dt>
        <dd style={{ margin: 0, color: 'var(--fx-text-primary)' }}>
          {anchorTypeLabel(anchor.anchor_type) || label}{subtype ? ` · ${subtype}` : ''}
        </dd>
        {stretchName && (
          <>
            <dt style={{ color: 'var(--fx-text-muted)' }}>Stretch</dt>
            <dd style={{ margin: 0, color: 'var(--fx-text-primary)' }}>{stretchName}</dd>
          </>
        )}
        <dt style={{ color: 'var(--fx-text-muted)' }}>Source</dt>
        <dd style={{ margin: 0, color: 'var(--fx-text-primary)' }}>{anchorSourceLabel(anchor.source)}</dd>
      </dl>

      {/* PLACEHOLDER — later work. The bot's explanation of why this spot
          matters (species, conditions to watch) and "Save to my map" land
          here. Both are out of scope for the Explore map UI step; this block
          only reserves the place. */}
      <div data-placeholder="anchor-explanation-and-save" style={{
        border: '1px dashed var(--fx-dashed-border)', borderRadius: 10, padding: '10px 12px',
        fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--fx-text-muted)', lineHeight: 1.5,
      }}>
        <div style={{ color: 'var(--fx-text-dim-2)', fontSize: 10, letterSpacing: '0.04em', textTransform: 'uppercase', marginBottom: 4 }}>
          Coming soon
        </div>
        Why this spot matters, which species use it and what to watch will show up here.
        <button
          disabled
          style={{
            display: 'block', width: '100%', marginTop: 10, minHeight: 44,
            background: 'var(--fx-card-fill-quiet)', border: '1px solid var(--fx-hairline)', borderRadius: 8,
            color: 'var(--fx-text-dim-2)', fontFamily: 'var(--fx-font-ui)', fontSize: 13, cursor: 'not-allowed',
          }}
        >
          Save to my map
        </button>
      </div>
    </div>
  );
}
