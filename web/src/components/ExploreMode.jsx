// Explore mode of the Map screen: a two-level map of named fishing stretches.
//
// Level 1 draws every stretch from GET /map/stretches over a dark base, each
// tappable and labelled. Tapping one zooms to it (Level 2) and draws its
// segment lines and anchors from GET /map/stretches/{id}/anchors.
//
// Anchors are located features ("two channels meet here"), not fish
// predictions — nothing on this screen says a spot holds fish.
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';
import { Fragment, useState, useEffect, useMemo, useCallback } from 'react';
import { MapContainer, TileLayer, Polyline, Marker, ZoomControl, useMap, useMapEvents } from 'react-leaflet';
import { getMapStretches, getStretchAnchors } from '../api';
import { colorBrass, colorMossLight, colorWetStone } from '../tokens';
import { anchorStyle, anchorSvg, anchorTypeLabel, bboxToBounds, lineLatLngs, ANCHOR_TYPES } from './anchors';
import AnchorSheet from './AnchorSheet';
import './explore-map.css';

const ONTARIO_CENTER = [44.2, -80.5];
const LABEL_MIN_ZOOM = 7;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function allBounds(features) {
  const pts = features
    .map((f) => bboxToBounds(f.properties?.bbox))
    .filter(Boolean)
    .flat();
  return pts.length ? L.latLngBounds(pts) : null;
}

// Flies the map to `view.bounds` whenever `view.id` changes.
function ViewController({ view }) {
  const map = useMap();
  useEffect(() => {
    if (!view?.bounds) return;
    map.flyToBounds(view.bounds, {
      paddingTopLeft: [24, view.topPad || 24],
      paddingBottomRight: [view.rightPad || 24, 24],
      maxZoom: view.maxZoom || 15,
      duration: 0.8,
    });
  }, [map, view?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

// Keeps the tapped anchor visible above the details sheet and tab bar.
function AnchorPan({ anchor }) {
  const map = useMap();
  const id = anchor?.props.anchor_id;
  useEffect(() => {
    if (!anchor) return;
    map.panInside(anchor.latlng, { paddingTopLeft: [40, 150], paddingBottomRight: [40, 380] });
  }, [map, id]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

function stretchLabelIcon(name, cls) {
  return L.divIcon({
    className: `fx-stretch-label ${cls}`,
    html: `<span>${escapeHtml(name)}</span>`,
    iconSize: [0, 0],
  });
}

function anchorIcon(type, selected) {
  return L.divIcon({
    className: `fx-anchor${selected ? ' is-selected' : ''}`,
    html: `<div class="fx-anchor-hit">${anchorSvg(type)}</div>`,
    iconSize: [40, 40],
    iconAnchor: [20, 20],
  });
}

function StretchLines({ features, selectedId, hideSelectedLine, onSelect }) {
  const [zoom, setZoom] = useState(null);
  const map = useMapEvents({ zoomend: () => setZoom(map.getZoom()) });
  const z = zoom ?? map.getZoom();

  return features.map((f) => {
    const p = f.properties || {};
    const positions = lineLatLngs(f.geometry);
    if (!positions) return null;
    const isSelected = p.stretch_id === selectedId;
    const dim = selectedId != null && !isSelected;
    const label = p.label_point;
    const click = { click: () => onSelect(f) };
    return (
      <Fragment key={p.stretch_id}>
        {!(isSelected && hideSelectedLine) && (
          <>
            <Polyline
              positions={positions}
              pathOptions={{
                color: isSelected ? colorBrass : colorMossLight,
                weight: dim ? 2 : 3.5,
                opacity: dim ? 0.3 : 0.95,
                lineCap: 'round',
              }}
              interactive={false}
            />
            {/* Fat invisible line: a finger-sized target for a thin river. */}
            <Polyline
              positions={positions}
              pathOptions={{ color: colorWetStone, weight: 22, opacity: 0, lineCap: 'round' }}
              eventHandlers={click}
            />
          </>
        )}
        {!isSelected && z >= LABEL_MIN_ZOOM && Array.isArray(label) && Number.isFinite(label[1]) && (
          <Marker
            position={[label[1], label[0]]}
            icon={stretchLabelIcon(p.name, dim ? 'is-dim' : '')}
            eventHandlers={click}
            keyboard={false}
          />
        )}
      </Fragment>
    );
  });
}

function StretchDetail({ detail, selectedAnchorId, onSelectAnchor }) {
  if (detail.status !== 'ready') return null;
  const features = detail.data.features || [];
  return features.map((f) => {
    const p = f.properties || {};
    if (p.feature === 'segment') {
      const positions = lineLatLngs(f.geometry);
      if (!positions) return null;
      return (
        <Polyline
          key={f.id || `seg-${p.ogf_id}`}
          positions={positions}
          pathOptions={{ color: colorBrass, weight: 4, opacity: 0.95, lineCap: 'round' }}
          interactive={false}
        />
      );
    }
    if (p.feature === 'anchor' && f.geometry?.type === 'Point') {
      const [lon, lat] = f.geometry.coordinates;
      return (
        <Marker
          key={p.anchor_id}
          position={[lat, lon]}
          icon={anchorIcon(p.anchor_type, p.anchor_id === selectedAnchorId)}
          eventHandlers={{ click: () => onSelectAnchor({ props: p, latlng: [lat, lon] }) }}
          keyboard={false}
          zIndexOffset={p.anchor_id === selectedAnchorId ? 1000 : 100}
        />
      );
    }
    return null;
  });
}

// What the Level 2 detail fetch came to, in words a reader can act on.
export function detailNotice(detail) {
  switch (detail.status) {
    case 'loading':
      return { text: 'Loading anchors…' };
    case 'missing':
      return { text: 'Detail map not available for this stretch yet. Showing its outline.' };
    case 'error':
      return { text: "Couldn't load this stretch's anchors. Showing its outline.", retry: true };
    case 'ready': {
      const anchors = (detail.data.features || []).filter((f) => f.properties?.feature === 'anchor');
      if (anchors.length === 0) return { text: 'No anchors mapped for this stretch yet.' };
      return null;
    }
    default:
      return null;
  }
}

function Legend({ types }) {
  if (types.length === 0) return null;
  return (
    <div className="fx-map-card" style={{
      position: 'absolute', top: 12, right: 12, zIndex: 1000,
      padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11,
    }}>
      {types.map((t) => (
        <div key={t} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span aria-hidden="true" style={{ display: 'inline-flex', width: 16, height: 16 }}
            dangerouslySetInnerHTML={{ __html: anchorSvg(t).replace('width="24" height="24"', 'width="16" height="16"') }} />
          <span>{anchorTypeLabel(t)}</span>
        </div>
      ))}
    </div>
  );
}

export default function ExploreMode() {
  const [stretches, setStretches] = useState({ status: 'loading', features: [] });
  const [selected, setSelected] = useState(null); // a Level 1 stretch feature
  const [detail, setDetail] = useState({ status: 'idle', data: null });
  const [anchor, setAnchor] = useState(null); // { props, latlng } of the tapped anchor
  const [view, setView] = useState(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getMapStretches()
      .then((data) => {
        if (cancelled) return;
        const features = data.features || [];
        setStretches({ status: 'ready', features });
        const bounds = allBounds(features);
        if (bounds) setView({ id: 'all', bounds, maxZoom: 8, topPad: 60, rightPad: 130 });
      })
      .catch(() => { if (!cancelled) setStretches({ status: 'error', features: [] }); });
    return () => { cancelled = true; };
  }, []);

  const stretchId = selected?.properties?.stretch_id;
  useEffect(() => {
    if (!stretchId) { setDetail({ status: 'idle', data: null }); return undefined; }
    let cancelled = false;
    setDetail({ status: 'loading', data: null });
    getStretchAnchors(stretchId)
      .then((data) => { if (!cancelled) setDetail({ status: 'ready', data }); })
      .catch((e) => {
        if (cancelled) return;
        // 404: backend without the anchors route, or a stretch not built yet.
        setDetail({ status: e.status === 404 ? 'missing' : 'error', data: null });
      });
    return () => { cancelled = true; };
  }, [stretchId, retryCount]);

  const selectStretch = useCallback((feature) => {
    setAnchor(null);
    setSelected(feature);
    const bounds = bboxToBounds(feature.properties?.bbox);
    if (bounds) setView({ id: `stretch:${feature.properties.stretch_id}`, bounds, topPad: 110 });
  }, []);

  const backToAll = useCallback(() => {
    setAnchor(null);
    setSelected(null);
    const bounds = allBounds(stretches.features);
    if (bounds) setView({ id: `all:${Date.now()}`, bounds, maxZoom: 8, topPad: 60, rightPad: 130 });
  }, [stretches.features]);

  const detailFeatures = detail.status === 'ready' ? detail.data.features || [] : [];
  const hasDetailLines = detailFeatures.some((f) => f.properties?.feature === 'segment');
  const presentTypes = useMemo(() => {
    const seen = new Set(
      detailFeatures.filter((f) => f.properties?.feature === 'anchor').map((f) => f.properties.anchor_type),
    );
    return Object.keys(ANCHOR_TYPES).filter((t) => seen.has(t));
  }, [detailFeatures]);

  const notice = selected ? detailNotice(detail) : null;
  const sp = selected?.properties;

  return (
    <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
      <MapContainer
        className="fx-explore-map"
        center={ONTARIO_CENTER}
        zoom={6}
        zoomControl={false}
        style={{ height: '100%', width: '100%' }}
      >
        <ZoomControl position="bottomright" />
        <TileLayer
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          attribution='&copy; OpenStreetMap &copy; CARTO'
          maxZoom={19}
        />
        <ViewController view={view} />
        <AnchorPan anchor={anchor} />
        <StretchLines
          features={stretches.features}
          selectedId={stretchId}
          hideSelectedLine={hasDetailLines}
          onSelect={selectStretch}
        />
        <StretchDetail detail={detail} selectedAnchorId={anchor?.props.anchor_id} onSelectAnchor={setAnchor} />
      </MapContainer>

      {!selected && (
        <div className="fx-map-card" style={{
          position: 'absolute', top: 12, left: 12, right: 12, zIndex: 1000,
          padding: '8px 14px', fontSize: 12, textAlign: 'center', pointerEvents: 'none',
        }}>
          {stretches.status === 'loading' && 'Loading fishing stretches…'}
          {stretches.status === 'error' && "Couldn't load the fishing stretches. Check your connection and reopen the map."}
          {stretches.status === 'ready' && stretches.features.length === 0 && 'No fishing stretches have been built yet.'}
          {stretches.status === 'ready' && stretches.features.length > 0 &&
            `${stretches.features.length} fishing stretches. Tap one to zoom in.`}
        </div>
      )}

      {selected && (
        <div className="fx-map-card" style={{
          position: 'absolute', top: 12, left: 12, right: presentTypes.length ? 150 : 12, zIndex: 1000,
          padding: '4px 12px 8px 4px',
        }}>
          <button
            onClick={backToAll}
            style={{
              minHeight: 44, padding: '0 10px', background: 'none', border: 'none', cursor: 'pointer',
              color: 'var(--fx-moss-light)', fontFamily: 'var(--fx-font-ui)', fontSize: 13, fontWeight: 500,
            }}
          >
            <span aria-hidden="true">← </span>All stretches
          </button>
          <div style={{ padding: '0 8px' }}>
            <div style={{ fontFamily: 'var(--fx-font-serif)', fontSize: 16, fontWeight: 600, color: 'var(--fx-text-primary-2)' }}>
              {sp.name}
            </div>
            <div style={{ fontSize: 11, marginTop: 2 }}>
              {[sp.river, sp.length_km != null ? `${Number(sp.length_km).toFixed(1)} km` : null]
                .filter(Boolean).join(' · ')}
            </div>
            {notice && (
              <div role="status" style={{ fontSize: 11, marginTop: 6, color: 'var(--fx-brass-light)' }}>
                {notice.text}
                {notice.retry && (
                  <button
                    onClick={() => setRetryCount((n) => n + 1)}
                    style={{
                      marginLeft: 8, minHeight: 32, padding: '0 10px', background: 'var(--fx-hairline)',
                      border: 'none', borderRadius: 6, color: 'var(--fx-text-primary)', fontSize: 11, cursor: 'pointer',
                    }}
                  >Retry</button>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      <Legend types={presentTypes} />

      {anchor && <AnchorSheet anchor={anchor.props} stretchName={sp?.name} onClose={() => setAnchor(null)} />}
    </div>
  );
}
