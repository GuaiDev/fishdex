import 'leaflet/dist/leaflet.css';
import { useState, useEffect } from 'react';
import { MapContainer, TileLayer, CircleMarker } from 'react-leaflet';
import { getMyStops } from '../api';
import { colorMoss, colorTextAsh } from '../tokens';
import InstrumentDial from '../components/InstrumentDial';
import ExploreMode from '../components/ExploreMode';
import '../fishdex-tokens.css';

function stopColor(productive) {
  return productive ? colorMoss : colorTextAsh;
}

function PersonalLayer({ stops, onStopClick }) {
  return stops.map(stop => (
    <CircleMarker
      key={stop.stop_id}
      center={[stop.lat, stop.lng]}
      radius={8}
      pathOptions={{
        color: stopColor(stop.productive),
        fillColor: stopColor(stop.productive),
        fillOpacity: 0.9,
        weight: 2,
      }}
      eventHandlers={{ click: () => onStopClick(stop) }}
    />
  ));
}

export default function Map() {
  const [mapMode, setMapMode] = useState('explore');
  const [myStops, setMyStops] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loadingStops, setLoadingStops] = useState(true);

  useEffect(() => {
    getMyStops()
      .then(data => {
        setMyStops(data.stops || []);
        setLoadingStops(false);
      })
      .catch(() => setLoadingStops(false));
  }, []);

  const defaultCenter = [43.5, -79.8];
  const defaultZoom = 10;

  return (
    <div style={{
      height: '100dvh', display: 'flex', flexDirection: 'column',
      background: 'radial-gradient(circle at 50% 0%, var(--fx-bg-grad-1), var(--fx-bg-grad-2) 45%, var(--fx-bg-grad-3) 100%)',
    }}>

      {/* Header */}
      <div style={{
        padding: '12px 16px',
        background: 'var(--fx-canvas)',
        borderBottom: '1px solid var(--fx-hairline)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        zIndex: 1000,
        flexShrink: 0,
      }}>
        <h1 style={{ fontFamily: 'var(--fx-font-serif)', fontWeight: 600, fontSize: 17, color: 'var(--fx-text-primary-2)', margin: 0 }}>Map</h1>

        <div style={{
          display: 'flex',
          background: 'var(--fx-card-fill)',
          border: '1px solid var(--fx-hairline)',
          borderRadius: 8,
          padding: 2,
          gap: 2,
        }}>
          {['explore', 'personal'].map(m => (
            <button
              key={m}
              onClick={() => { setMapMode(m); setSelected(null); }}
              style={{
                padding: '5px 12px',
                borderRadius: 6,
                border: 'none',
                background: mapMode === m ? 'var(--fx-moss-light)' : 'transparent',
                color: mapMode === m ? 'var(--fx-on-accent)' : 'var(--fx-text-muted)',
                fontFamily: 'var(--fx-font-ui)',
                fontSize: 12,
                fontWeight: 500,
                cursor: 'pointer',
                transition: 'all 0.15s',
                textTransform: 'capitalize',
              }}
            >
              {m}
            </button>
          ))}
        </div>

      </div>

      {/* Map */}
      {mapMode === 'explore' && <ExploreMode />}

      {mapMode === 'personal' && (
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
          <PersonalLayer stops={myStops} onStopClick={setSelected} />
        </MapContainer>

        {!loadingStops && myStops.length === 0 && (
          <div style={{
            position: 'absolute', top: '50%', left: '50%',
            transform: 'translate(-50%, -50%)',
            background: 'var(--fx-card-fill)',
            border: '1px solid var(--fx-hairline)',
            borderRadius: 14,
            padding: '20px 24px',
            textAlign: 'center',
            zIndex: 1000,
            maxWidth: 240,
          }}>
            <div aria-hidden="true" style={{ fontSize: 28, marginBottom: 8 }}>🎣</div>
            <div style={{ fontFamily: 'var(--fx-font-serif)', fontSize: 16, fontWeight: 600, color: 'var(--fx-text-primary-2)', marginBottom: 6 }}>
              Your map is empty
            </div>
            <div style={{ fontFamily: 'var(--fx-font-ui)', fontSize: 12, color: 'var(--fx-text-muted)', lineHeight: 1.5 }}>
              Log trips with GPS to see your spots appear here.
              Switch to Explore to find new water.
            </div>
          </div>
        )}

      </div>
      )}

      {/* Bottom sheet. Intentionally a lightweight detail panel, not a true
          modal: no focus trap, no Escape-to-close, background map stays
          interactive. role="dialog" + aria-label identify it as a distinct
          region for assistive tech without the false promise of aria-modal. */}
      {selected && (
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
            onClick={() => setSelected(null)}
            aria-label="Close"
            style={{
              position: 'absolute', top: 4, right: 8,
              width: 44, height: 44,
              background: 'none', border: 'none',
              color: 'var(--fx-text-muted)', fontSize: 20, cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          ><span aria-hidden="true">×</span></button>

          {/* Personal stop detail */}
          {selected.stop_id && (
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
            </>
          )}
        </div>
      )}
    </div>
  );
}
