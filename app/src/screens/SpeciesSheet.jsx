import { useEffect, useRef } from 'react';
import { resolveAssetUrl } from '../api.js';
import { formatInches } from '../fishdexModel.js';
import { Thumb } from './SpeciesCard.jsx';

// The catch detail: the one purposeful dark moment, so the sheet pins the dark
// tokens whatever the rest of the app is doing. A native <dialog> gives focus
// containment, Escape-to-close and a backdrop without any extra code.
export default function SpeciesSheet({ species, onClose }) {
  const ref = useRef(null);
  const c = species.caught;

  useEffect(() => {
    const dialog = ref.current;
    dialog.showModal();
    dialog.querySelector('h2').focus(); // not the Done button: no focus ring on open
    return () => dialog.close();
  }, []);

  return (
    <dialog
      ref={ref}
      className="sheet"
      data-theme="dark"
      aria-labelledby="sheet-title"
      // A StrictMode remount closes and reopens the dialog; only a real close counts.
      onClose={() => !ref.current.open && onClose()}
      onClick={(e) => e.target === ref.current && ref.current.close()}
    >
      <div className="grabber" aria-hidden="true" />
      <div className="sheet-photo">
        {c.representativePhotoUrl ? (
          <img
            className="own-photo"
            src={resolveAssetUrl(c.representativePhotoUrl)}
            alt={`Your ${species.commonName}`}
          />
        ) : (
          <Thumb species={species} large />
        )}
      </div>
      <div className="sheet-title">
        <h2 id="sheet-title" tabIndex={-1}>{species.commonName}</h2>
        {species.scientificName && <p>{species.scientificName}</p>}
      </div>
      <dl className="kv">
        <div className="r">
          <dt>Logged</dt>
          <dd className="num">×{c.count}</dd>
        </div>
        {c.maxLengthInches != null && (
          <div className="r">
            <dt>Longest</dt>
            <dd className="num">
              {formatInches(c.maxLengthInches)}
              {c.isPersonalBest && ' · PB'}
            </dd>
          </div>
        )}
        <div className="r">
          <dt>Family</dt>
          <dd>{species.familyCommonName || 'Other'}</dd>
        </div>
      </dl>
      <div className="sheet-actions">
        <button type="button" className="btn btn-primary" onClick={() => ref.current.close()}>
          Done
        </button>
      </div>
    </dialog>
  );
}
