import 'leaflet/dist/leaflet.css';
import { useState, useEffect, useRef, useCallback } from 'react';
import { MapContainer, TileLayer, CircleMarker, useMapEvents } from 'react-leaflet';
import { addRecalledVisit, createPin, getMapSegments, getPins, linkStopToPin } from '../api';
import { colorHeatHigh, colorHeatMed, colorHeatMid, colorHeatLow, colorHeatMin, colorMoss, colorTextAsh, colorBrass } from '../tokens';
import InstrumentDial from '../components/InstrumentDial';
import PinSheet, { RecalledForm } from '../components/PinSheet';
import { formatDistance, pinUi, usePinLabels } from '../pins';
import '../fishdex-tokens.css';

function scoreColor(score) {
  if (score >= 0.7) return colorHeatHigh;
  if (score >= 0.5) return colorHeatMed;
  if (score >= 0.3) return colorHeatMid;
  if (score >= 0.1) return colorHeatLow;
  return colorHeatMin;
}

function stopColor(productive) {
  return productive ? colorMoss : colorTextAsh;
}

function accessCoverageLabel(segment) {
  if (segment.access_is_measured === true) return 'Access measured here';
  if (segment.access_is_measured === false) return 'Access not measured here';
  return 'Access coverage unknown';
}

function accessCoverageTone(segment) {
  if (segment.access_is_measured === true) return 'var(--fx-moss-light)';
  if (segment.access_is_measured === false) return 'var(--fx-brass-light)';
  return 'var(--fx-text-muted)';
}

function ExploreLayer({ mode, onSegmentClick }) {
  const [segments, setSegments] = useState([]);
  const fetchTimeout = useRef(null);

  const fetchSegments = useCallback((map) => {
    const zoom = map.getZoom();
    if (zoom < 11) { setSegments([]); return; }

    clearTimeout(fetchTimeout.current);
    const bounds = map.getBounds();
    fetchTimeout.current = setTimeout(async () => {
      try {
        const data = await getMapSegments({
          north: bounds.getNorth(),
          south: bounds.getSouth(),
          east: bounds.getEast(),
          west: bounds.getWest(),
        }, mode);
        setSegments(data.segments || []);
      } catch (e) {
        console.error('Failed to fetch segments:', e);
      }
    }, 300);
  }, [mode]);

  const map = useMapEvents({
    moveend: () => fetchSegments(map),
    zoomend: () => fetchSegments(map),
  });

  useEffect(() => {
    fetchSegments(map);
  }, [map, fetchSegments]);

  return segments.map(seg => (
    <CircleMarker
      key={seg.ogf_id}
      center={[seg.lat, seg.lng]}
      radius={6}
      pathOptions={{
        color: scoreColor(seg.score),
        fillColor: scoreColor(seg.score),
        fillOpacity: 0.8,
        weight: 1,
      }}
      eventHandlers={{ click: () => onSegmentClick(seg) }}
    />
  ));
}

// Saved pins are hollow rings, Visited pins are filled; a favourite gets a
// brass outline. Stops not linked to any pin stay as small dots so a logged
// trip never disappears from the map just because it is unpinned.
function PersonalLayer({ pins, stops, onPinClick, onStopClick }) {
  return (
    <>
      {stops.map(stop => (
        <CircleMarker
          key={`stop-${stop.stop_id}`}
          center={[stop.lat, stop.lng]}
          radius={5}
          pathOptions={{
            color: stopColor(stop.productive),
            fillColor: stopColor(stop.productive),
            fillOpacity: 0.7,
            weight: 1,
          }}
          eventHandlers={{ click: () => onStopClick(stop) }}
        />
      ))}
      {pins.map(pin => (
        <CircleMarker
          key={`pin-${pin.id}`}
          center={[pin.lat, pin.lng]}
          radius={10}
          pathOptions={{
            color: pin.is_favourite ? colorBrass : colorMoss,
            fillColor: colorMoss,
            fillOpacity: pin.state === 'visited' ? 0.85 : 0.08,
            weight: pin.is_favourite ? 4 : 2,
            dashArray: pin.state === 'visited' && pin.has_recalled && pin.visits === 0 ? '3 3' : null,
          }}
          eventHandlers={{ click: () => onPinClick(pin) }}
        />
      ))}
    </>
  );
}

function PlacePinOnTap({ onPlace }) {
  useMapEvents({ click: e => onPlace({ lat: e.latlng.lat, lng: e.latlng.lng }) });
  return null;
}

// A new pin at a tapped point: a Saved pin, or, with "I've fished here
// before", one carrying a recalled visit.
function NewPinForm({ at, onDone }) {
  const [name, setName] = useState('');
  const [notes, setNotes] = useState('');
  const [recalled, setRecalled] = useState(false);
  const [error, setError] = useState('');

  async function save(recalledFields) {
    const created = await createPin({ name: name.trim(), lat: at.lat, lng: at.lng, notes: notes.trim() || null });
    if (recalledFields) await addRecalledVisit(created.pin.id, recalledFields);
    onDone(created.pin.id);
  }

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <div style={pinUi.heading}>New pin</div>
      <input style={pinUi.input} placeholder="Name this spot" value={name} onChange={e => setName(e.target.value)} aria-label="Pin name" />
      <textarea style={{ ...pinUi.input, minHeight: 54 }} placeholder="Notes (optional, private)" value={notes} onChange={e => setNotes(e.target.value)} aria-label="Pin notes" />
      <label style={{ ...pinUi.muted, display: 'flex', gap: 6, alignItems: 'center' }}>
        <input type="checkbox" checked={recalled} onChange={e => setRecalled(e.target.checked)} />
        I've fished here before
      </label>
      {recalled ? (
        <RecalledForm
          submitLabel="Save pin"
          onSubmit={fields => {
            if (!name.trim()) throw new Error('Name the spot first');
            return save(fields);
          }}
        />
      ) : (
        <div>
          <button
            type="button"
            style={pinUi.primary}
            disabled={!name.trim()}
            onClick={() => save(null).catch(err => setError(err.message))}
          >
            Save pin
          </button>
        </div>
      )}
      {error && <div style={pinUi.error}>{error}</div>}
    </div>
  );
}

// "Pin this spot" for a logged stop, plus a one-tap link to any pin already
// within the link radius.
function StopPinActions({ stop, onDone }) {
  const labels = usePinLabels();
  const [name, setName] = useState(stop.location && stop.location !== 'Unknown' ? stop.location : '');
  const [naming, setNaming] = useState(false);
  const [error, setError] = useState('');

  const run = fn => fn().then(onDone).catch(err => setError(err.message));

  return (
    <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
      {stop.candidates.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {stop.candidates.map(c => (
            <button key={c.pin_id} type="button" style={pinUi.button}
              onClick={() => run(() => linkStopToPin(c.pin_id, stop.stop_id).then(() => c.pin_id))}>
              Link to {c.name} · {formatDistance(c.distance_m)}
            </button>
          ))}
        </div>
      )}
      {naming ? (
        <div style={{ display: 'flex', gap: 8 }}>
          <input style={pinUi.input} value={name} onChange={e => setName(e.target.value)} placeholder="Name this spot" aria-label="Pin name" />
          <button type="button" style={pinUi.primary} disabled={!name.trim()}
            onClick={() => run(() => createPin({ name: name.trim(), lat: stop.lat, lng: stop.lng, from_stop_id: stop.stop_id }).then(r => r.pin.id))}>
            Save
          </button>
        </div>
      ) : (
        <div>
          <button type="button" style={pinUi.primary} onClick={() => setNaming(true)}>📍 Pin this spot</button>
          {labels && (
            <span style={{ ...pinUi.muted, marginLeft: 8 }}>
              Trips within {labels.link_radius_m} m link to it.
            </span>
          )}
        </div>
      )}
      {error && <div style={pinUi.error}>{error}</div>}
    </div>
  );
}

export default function Map() {
  const [mapMode, setMapMode] = useState('personal');
  const [exploreMode, setExploreMode] = useState('balanced');
  const [pins, setPins] = useState([]);
  const [myStops, setMyStops] = useState([]);
  const [selected, setSelected] = useState(null);
  const [selectedPinId, setSelectedPinId] = useState(null);
  const [placing, setPlacing] = useState(false);
  const [newPinAt, setNewPinAt] = useState(null);
  const [loadingStops, setLoadingStops] = useState(true);

  const reload = useCallback(() => {
    return getPins()
      .then(data => {
        setPins(data.pins || []);
        setMyStops(data.unpinned_stops || []);
        setLoadingStops(false);
      })
      .catch(() => setLoadingStops(false));
  }, []);

  useEffect(() => { reload(); }, [reload]);

  function closeSheet() {
    setSelected(null);
    setSelectedPinId(null);
    setNewPinAt(null);
  }

  function openPin(pinId) {
    closeSheet();
    setSelectedPinId(pinId);
  }

  function openOther(item) {
    closeSheet();
    setSelected(item);
  }

  const defaultCenter = [43.5, -79.8];
  const defaultZoom = 10;

  return (
    <div style={{
      height: '100dvh', display: 'flex', flexDirection: 'column',
      background: 'radial-gradient(circle at 50% 0%, var(--fx-bg-grad-1), var(--fx-bg-grad-2) 45%, var(--fx-bg-grad-3) 100%)',
    }}>

      {/* Header */}
      <div style={{
        padding: '14px 18px 12px',
        background: 'var(--fx-canvas)',
        borderBottom: '1px solid var(--fx-hairline)',
        zIndex: 1000,
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 13 }}>
          <div>
            <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 10, fontWeight: 600, letterSpacing: '.16em', textTransform: 'uppercase', color: 'var(--fx-moss-light)', marginBottom: 4 }}>
              Field map
            </div>
            <h1 style={{ fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 25, lineHeight: 1, color: 'var(--fx-text-primary-2)', margin: 0 }}>
              Credit River watershed
            </h1>
          </div>
          <button aria-label="Search the map" style={{ width: 38, height: 38, borderRadius: 20, border: '1px solid var(--fx-hairline)', background: 'var(--fx-card-fill-dark)', color: 'var(--fx-text-muted)', fontSize: 18, cursor: 'pointer' }}>
            ⌕
          </button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ flex: 1, display: 'flex', background: 'var(--fx-card-fill)', border: '1px solid var(--fx-hairline)', borderRadius: 10, padding: 3, gap: 3 }}>
            {[['personal', 'My water'], ['explore', 'Find water']].map(([m, label]) => (
              <button key={m} onClick={() => { setMapMode(m); closeSheet(); setPlacing(false); }} style={{ flex: 1, padding: '7px 9px', borderRadius: 7, border: 'none', background: mapMode === m ? 'var(--fx-moss-light)' : 'transparent', color: mapMode === m ? 'var(--fx-on-accent)' : 'var(--fx-text-muted)', fontFamily: 'var(--fx-font-ui)', fontSize: 11, fontWeight: 600, cursor: 'pointer' }}>
                {label}
              </button>
            ))}
          </div>
          {mapMode === 'personal' && (
            <button
              type="button"
              onClick={() => { closeSheet(); setPlacing(p => !p); }}
              aria-pressed={placing}
              style={{
                ...pinUi.button,
                padding: '8px 12px',
                borderRadius: 9,
                fontSize: 11,
                fontWeight: 600,
                borderColor: placing ? 'var(--fx-moss-light)' : 'var(--fx-hairline)',
                color: placing ? 'var(--fx-moss-lightest)' : 'var(--fx-text-primary)',
              }}
            >
              {placing ? 'Cancel' : '+ Pin'}
            </button>
          )}
          {mapMode === 'explore' && (
            <select value={exploreMode} onChange={e => setExploreMode(e.target.value)} aria-label="Discovery mode" style={{ width: 116, background: 'var(--fx-card-fill)', border: '1px solid var(--fx-hairline)', borderRadius: 9, color: 'var(--fx-text-primary)', fontFamily: 'var(--fx-font-ui)', fontSize: 11, padding: '8px 7px', cursor: 'pointer' }}>
              <option value="balanced">Balanced</option>
              <option value="easy">Easy access</option>
              <option value="adventure">Adventure</option>
            </select>
          )}
        </div>
      </div>

      {/* Map */}
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        <MapContainer
          center={defaultCenter}
          zoom={defaultZoom}
          style={{ height: '100%', width: '100%' }}
        >
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            attribution='&copy; OpenStreetMap &copy; CARTO'
            maxZoom={19}
          />

          {mapMode === 'personal' && (
            <PersonalLayer
              pins={pins}
              stops={myStops}
              onPinClick={pin => openPin(pin.id)}
              onStopClick={openOther}
            />
          )}

          {mapMode === 'personal' && placing && (
            <PlacePinOnTap onPlace={at => { setPlacing(false); closeSheet(); setNewPinAt(at); }} />
          )}

          {mapMode === 'explore' && (
            <ExploreLayer mode={exploreMode} onSegmentClick={openOther} />
          )}
        </MapContainer>

        {mapMode === 'personal' && placing && (
          <div style={{
            position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)',
            background: 'var(--fx-card-fill)', border: '1px solid var(--fx-hairline)', borderRadius: 20,
            padding: '6px 14px', zIndex: 1000, pointerEvents: 'none', ...pinUi.muted,
          }}>
            Tap the map where the pin goes
          </div>
        )}

        {mapMode === 'personal' && !loadingStops && myStops.length === 0 && pins.length === 0 && !placing && (
          <div style={{
            position: 'absolute', bottom: 76, left: 16, right: 16,
            background: 'color-mix(in srgb, var(--fx-card-fill) 94%, transparent)',
            border: '1px solid var(--fx-hairline)',
            borderRadius: 13,
            padding: '12px 14px',
            textAlign: 'left',
            zIndex: 1000,
            display: 'flex',
            alignItems: 'center',
            gap: 11,
          }}>
            <div aria-hidden="true" style={{ width: 30, height: 30, borderRadius: 9, background: 'var(--fx-moss-fill-dim)', color: 'var(--fx-moss-light)', display: 'grid', placeItems: 'center', fontSize: 17 }}>⌖</div>
            <div>
              <div style={{ fontFamily: 'var(--fx-font-serif)', fontSize: 15, fontWeight: 600, color: 'var(--fx-text-primary-2)', marginBottom: 2 }}>Your water will appear here</div>
              <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 11, color: 'var(--fx-text-muted)', lineHeight: 1.4 }}>Log a trip with GPS, tap + Pin to save a spot, or switch to Find water.</div>
            </div>
          </div>
        )}

        {mapMode === 'explore' && (
          <div style={{ position: 'absolute', top: 14, left: 14, zIndex: 1000, pointerEvents: 'none' }}>
            <div style={{ padding: '7px 10px', background: 'color-mix(in srgb, var(--fx-card-fill) 90%, transparent)', border: '1px solid var(--fx-hairline)', borderRadius: 10, boxShadow: '0 5px 16px rgba(0,0,0,.2)', fontFamily: 'var(--fx-font-ui)', fontSize: 10, color: 'var(--fx-text-muted)' }}>
              <span style={{ color: 'var(--fx-moss-light)', fontWeight: 600 }}>Discovery signals</span> · not a fish prediction
            </div>
          </div>
        )}

        {mapMode === 'explore' && (
          <div style={{
            position: 'absolute',
            bottom: 82, left: 16,
            transform: 'none',
            background: 'color-mix(in srgb, var(--fx-card-fill) 90%, transparent)',
            border: '1px solid var(--fx-hairline)',
            borderRadius: 13,
            padding: '9px 11px',
            fontFamily: 'var(--fx-font-ui)',
            fontSize: 11,
            color: 'var(--fx-text-muted)',
            zIndex: 1000,
            pointerEvents: 'none',
            whiteSpace: 'normal',
          }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 7 }}>
              {[['var(--color-heat-high)', 'high'], ['var(--color-heat-mid)', 'mid'], ['var(--color-heat-low)', 'low']].map(([color, label]) => <span key={label} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><i style={{ width: 7, height: 7, borderRadius: '50%', background: color, display: 'block' }} />{label}</span>)}
            </div>
            <span>Zoom in to inspect individual reaches</span>
          </div>
        )}
      </div>

      {/* Bottom sheet. Intentionally a lightweight detail panel, not a true
          modal: no focus trap, no Escape-to-close, background map stays
          interactive. role="dialog" + aria-label identify it as a distinct
          region for assistive tech without the false promise of aria-modal. */}
      {(selected || selectedPinId || newPinAt) && (
        <div role="dialog" aria-label="Location details" style={{
          position: 'fixed',
          bottom: 64, left: 0, right: 0,
          maxWidth: 480, margin: '0 auto',
          background: 'var(--fx-card-fill)',
          borderTop: '1px solid var(--fx-hairline)',
          borderRadius: '16px 16px 0 0',
          padding: '16px 16px 24px',
          zIndex: 2000,
        }}>
          <button
            onClick={closeSheet}
            aria-label="Close"
            style={{
              position: 'absolute', top: 4, right: 8,
              width: 44, height: 44,
              background: 'none', border: 'none',
              color: 'var(--fx-text-muted)', fontSize: 20, cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          ><span aria-hidden="true">×</span></button>

          {selectedPinId && (
            <PinSheet
              pinId={selectedPinId}
              otherPins={pins}
              onChanged={reload}
              onClose={closeSheet}
            />
          )}

          {newPinAt && (
            <NewPinForm at={newPinAt} onDone={pinId => reload().then(() => openPin(pinId))} />
          )}

          {/* Personal stop detail — a logged stop not yet linked to a pin */}
          {selected?.stop_id && (
            <>
              <div style={{ fontFamily: 'var(--fx-font-serif)', fontSize: 16, fontWeight: 600, color: 'var(--fx-text-primary-2)', marginBottom: 4 }}>
                {selected.location}
              </div>
              <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--fx-text-muted)', marginBottom: 10 }}>
                {selected.date}
              </div>
              {(selected.conditions?.air_temp_c != null || selected.conditions?.pressure_hpa != null) && (
                <div style={{ display: 'flex', gap: 12, marginBottom: 10 }}>
                  {selected.conditions?.air_temp_c != null && (
                    <InstrumentDial value={selected.conditions.air_temp_c} unit="°C" label="Air temp" min={-10} max={35} />
                  )}
                  {selected.conditions?.pressure_hpa != null && (
                    <InstrumentDial value={selected.conditions.pressure_hpa} unit="" label="Pressure hPa" min={970} max={1040} />
                  )}
                </div>
              )}
              {selected.species.length > 0 ? (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {selected.species.map((sp, i) => (
                    <span key={i} style={{
                      background: 'var(--fx-card-fill-quiet)', color: 'var(--fx-moss-light)',
                      fontFamily: 'var(--fx-font-ui)',
                      borderRadius: 6, padding: '3px 8px', fontSize: 11,
                    }}>{sp}</span>
                  ))}
                </div>
              ) : (
                <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--fx-text-muted)' }}>No fish logged</div>
              )}
              {selected.technique && (
                <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 11, color: 'var(--fx-text-muted)', marginTop: 8 }}>
                  {selected.technique}
                  {selected.gear ? ` · ${selected.gear}` : ''}
                </div>
              )}
              <StopPinActions
                key={selected.stop_id}
                stop={selected}
                onDone={pinId => reload().then(() => openPin(pinId))}
              />
            </>
          )}

          {/* Explore segment detail */}
          {selected?.ogf_id && (
            <>
              <div style={{ fontFamily: 'var(--fx-font-serif)', fontSize: 16, fontWeight: 600, color: 'var(--fx-text-primary-2)', marginBottom: 4 }}>
                {selected.watercourse_name || selected.nearest_named_stream || 'Unnamed stream'}
              </div>
              <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--fx-text-muted)', marginBottom: 10 }}>
                {selected.stream_order ? `Stream order ${selected.stream_order}` : 'Stream order not recorded'}
                {selected.is_confluence ? ' · Confluence' : ''}
                {selected.connected_to_waterbody ? ' · Connected to lake/river' : ''}
              </div>

              <div style={{ marginBottom: 10 }}>
                <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 11, color: 'var(--fx-text-muted)', marginBottom: 4 }}>
                  Discovery signal
                </div>
                <div style={{
                  height: 6, background: 'var(--fx-hairline)', borderRadius: 3, overflow: 'hidden',
                }}>
                  <div style={{
                    height: '100%',
                    width: '100%',
                    transform: `scaleX(${selected.score || 0})`,
                    transformOrigin: 'left',
                    background: scoreColor(selected.score),
                    borderRadius: 3,
                    transition: 'transform 0.3s',
                  }} />
                </div>
                <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 10, color: 'var(--fx-text-muted)', marginTop: 2 }}>
                  {((selected.score || 0) * 100).toFixed(0)} · based on pressure, structure, remoteness and access
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '8px 10px', background: 'var(--fx-card-fill-quiet)', borderRadius: 8, fontFamily: 'var(--fx-font-ui)', fontSize: 11, color: accessCoverageTone(selected) }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: accessCoverageTone(selected) }} />
                {accessCoverageLabel(selected)}
              </div>
              <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 10, lineHeight: 1.45, color: 'var(--fx-text-muted)', marginTop: 8 }}>
                This signal helps surface overlooked water. It does not say fish are present.
              </div>

              <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                {selected.google_maps_url && (
                  <a
                    href={selected.google_maps_url}
                    target="_blank"
                    rel="noreferrer"
                    style={{
                      flex: 1, padding: '8px',
                      background: 'var(--fx-hairline)', color: 'var(--fx-text-primary)',
                      fontFamily: 'var(--fx-font-ui)',
                      borderRadius: 8, fontSize: 12, fontWeight: 500,
                      textAlign: 'center', textDecoration: 'none',
                    }}
                  >
                    Open in Maps
                  </a>
                )}
                {selected.swoop_url && (
                  <a
                    href={selected.swoop_url}
                    target="_blank"
                    rel="noreferrer"
                    style={{
                      flex: 1, padding: '8px',
                      background: 'var(--fx-hairline)', color: 'var(--fx-text-primary)',
                      fontFamily: 'var(--fx-font-ui)',
                      borderRadius: 8, fontSize: 12, fontWeight: 500,
                      textAlign: 'center', textDecoration: 'none',
                    }}
                  >
                    Satellite view
                  </a>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
