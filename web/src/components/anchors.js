// What the Explore map knows how to say about an anchor. Shapes differ as well
// as colors so the four kinds in the brief (lake mouth, confluence, dam with
// tailwater, access point) stay distinguishable without relying on hue alone.
//
// Source of truth for the contract is GET /map/stretches/{id}/anchors:
//   anchor_type: confluence | dam | tailwater | lake_mouth | access_point
//   subtype:     dam | weir | lock (dams) or parking | trail_head | boat_launch |
//                fishing_spot | road_crossing (access points); otherwise null
//   source:      ohn_topology | osm
import { colorBrass, colorMossLight, colorRust, colorBone } from '../tokens';

export const ANCHOR_TYPES = {
  lake_mouth: { label: 'Lake mouth', color: colorBrass, shape: 'ring' },
  confluence: { label: 'Confluence', color: colorMossLight, shape: 'diamond' },
  dam: { label: 'Dam', color: colorRust, shape: 'square' },
  tailwater: { label: 'Tailwater', color: colorRust, shape: 'triangle' },
  access_point: { label: 'Access point', color: colorBone, shape: 'dot' },
};

const FALLBACK = { label: 'Feature', color: colorBone, shape: 'dot' };

const SUBTYPE_LABELS = {
  dam: 'Dam',
  weir: 'Weir',
  lock: 'Lock',
  parking: 'Parking',
  trail_head: 'Trail head',
  boat_launch: 'Boat launch',
  fishing_spot: 'Fishing spot',
  road_crossing: 'Road crossing',
};

const SOURCE_LABELS = {
  ohn_topology: 'Ontario Hydro Network (found from where stream channels join)',
  osm: 'OpenStreetMap',
};

export function anchorStyle(type) {
  return ANCHOR_TYPES[type] || FALLBACK;
}

export function anchorTypeLabel(type) {
  return anchorStyle(type).label;
}

// A subtype only adds information when it differs from the type's own label
// ("Dam" / "dam" says nothing; "Weir" / "dam" does).
export function anchorSubtypeLabel(type, subtype) {
  if (!subtype) return null;
  const label = SUBTYPE_LABELS[subtype] || subtype.replace(/_/g, ' ');
  return label === anchorTypeLabel(type) ? null : label;
}

export function anchorSourceLabel(source) {
  return SOURCE_LABELS[source] || source || 'Unknown source';
}

// The name a source gave the anchor, or a plain description. Names are never
// invented upstream, so an unnamed anchor says what it is instead.
export function anchorTitle(props) {
  if (props.name) return props.name;
  const sub = anchorSubtypeLabel(props.anchor_type, props.subtype);
  const base = anchorTypeLabel(props.anchor_type);
  return sub ? `Unnamed ${sub.toLowerCase()}` : `Unnamed ${base.toLowerCase()}`;
}

// GeoJSON [lon, lat] -> Leaflet [lat, lng]. Handles LineString (one path) and
// MultiLineString (several); react-leaflet's Polyline takes either nesting.
export function lineLatLngs(geometry) {
  if (!geometry || !Array.isArray(geometry.coordinates)) return null;
  const flip = (coords) => coords.map(([lon, lat]) => [lat, lon]);
  if (geometry.type === 'LineString') return flip(geometry.coordinates);
  if (geometry.type === 'MultiLineString') return geometry.coordinates.map(flip);
  return null;
}

// [min_lon, min_lat, max_lon, max_lat] -> Leaflet [[south, west], [north, east]].
export function bboxToBounds(bbox) {
  if (!Array.isArray(bbox) || bbox.length !== 4) return null;
  const [w, s, e, n] = bbox;
  if (![w, s, e, n].every(Number.isFinite)) return null;
  return [[s, w], [n, e]];
}

// Inline SVG for a marker. Fixed viewBox so every shape sits in the same
// 24px box; the outer element is the (larger) tap target.
export function anchorSvg(type) {
  const { color, shape } = anchorStyle(type);
  const stroke = 'stroke="#131410" stroke-width="1.5"';
  const body = {
    ring: `<circle cx="12" cy="12" r="7" fill="none" stroke="${color}" stroke-width="3.5"/><circle cx="12" cy="12" r="2.5" fill="${color}"/>`,
    diamond: `<path d="M12 3 21 12 12 21 3 12Z" fill="${color}" ${stroke}/>`,
    square: `<rect x="5" y="5" width="14" height="14" rx="1.5" fill="${color}" ${stroke}/>`,
    triangle: `<path d="M12 4 21 20H3Z" fill="${color}" ${stroke}/>`,
    dot: `<circle cx="12" cy="12" r="6" fill="${color}" ${stroke}/>`,
  }[shape];
  return `<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">${body}</svg>`;
}
