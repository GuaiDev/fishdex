import { resolveAssetUrl } from '../api.js';
import { initials } from '../fishdexModel.js';

export function Thumb({ species, large = false }) {
  const image = species.image ?? species.caught?.image;
  if (image?.status === 'approved') {
    return <img src={resolveAssetUrl(large ? image.url : image.thumbUrl)} alt="" loading="lazy" />;
  }
  return <span className="ini">{initials(species.commonName)}</span>;
}

// A species you have caught opens its detail; one you have not is a quiet
// outline, there to show what is left to find.
export default function SpeciesCard({ species, onOpen }) {
  const c = species.caught;
  const body = (
    <>
      <div className={c ? 'thumb' : 'thumb miss'}>
        {c ? <Thumb species={species} /> : <span className="ini">{initials(species.commonName)}</span>}
        {c?.isNewFind && <span className="dot" aria-hidden="true" />}
      </div>
      <div className="card-body">
        <div className="card-name">{species.commonName}</div>
        {species.scientificName && <div className="card-sci">{species.scientificName}</div>}
        <div className="card-foot">
          <span className="chip">{species.familyCommonName || 'Other'}</span>
          {c ? (
            <>
              <span className="cnt num">
                ×{c.count}
                {c.isPersonalBest && ' · PB'}
              </span>
              {c.isNewFind && <span className="tag-new">New</span>}
            </>
          ) : (
            <span className="cnt">not logged</span>
          )}
        </div>
      </div>
    </>
  );

  return c ? (
    <button type="button" className="card" onClick={onOpen}>
      {body}
    </button>
  ) : (
    <article className="card">{body}</article>
  );
}
