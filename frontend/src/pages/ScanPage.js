import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../services/api';
import { RARITY_VOCAB } from '../data/options';

/**
 * ScanPage — point the Pi camera at a Yu-Gi-Oh card, capture a short tilt-burst,
 * and get back the card + its RARITY + the price for that exact rarity printing.
 *
 * Why a tilt-burst: the same card prints in many rarities (esp. Rarity
 * Collection), and the only reliable way to tell them apart is how the foil
 * moves under tilt. The user tilts the card during the ~2s capture; the backend
 * measures the foil (OpenCV) and asks DeepSeek to name the rarity from those
 * cues + the frames.
 *
 * Confirm-first: nothing is added until the user clicks "Add to collection".
 * Rarity can be overridden from the dropdown (re-prices live). The detected
 * confidence is shown so a low-confidence guess gets a second look — the data
 * for later graduating high-confidence scans to auto-add.
 */

const fmtPct = (n) => `${Math.round((n || 0) * 100)}%`;
const fmtPrice = (p) =>
  p === null || p === undefined ? '—' : `$${Number(p).toFixed(2)}`;
const LOW_CONFIDENCE = 0.6;

const ScanPage = () => {
  const navigate = useNavigate();
  const [previewNonce, setPreviewNonce] = useState(0);
  const [previewBroken, setPreviewBroken] = useState(false);
  const [phase, setPhase] = useState('idle');     // idle | capturing | done | error
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [chosenRarity, setChosenRarity] = useState('');
  const [price, setPrice] = useState(null);
  const [priceSource, setPriceSource] = useState(null);
  const [repricing, setRepricing] = useState(false);
  const [saveState, setSaveState] = useState('idle'); // idle | saving | saved | merged | error
  const [saveMsg, setSaveMsg] = useState(null);

  const previewSrc = useMemo(
    () => `${api.scanPreviewUrl()}?n=${previewNonce}`,
    [previewNonce],
  );

  const capture = async () => {
    setPhase('capturing');
    setError(null);
    setResult(null);
    setSaveState('idle');
    setSaveMsg(null);
    try {
      const { data } = await api.scanCapture('yugioh');
      setResult(data);
      setChosenRarity(data?.rarity?.rarity && data.rarity.rarity !== 'unknown'
        ? data.rarity.rarity : '');
      setPrice(data?.price?.tcgplayer_price ?? null);
      setPriceSource(data?.price?.source ?? null);
      setPhase('done');
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Scan failed');
      setPhase('error');
    }
  };

  // Re-price when the user overrides the rarity (a different rarity = a
  // different printing = a different price).
  const onChangeRarity = async (newRarity) => {
    setChosenRarity(newRarity);
    const ready = result?.ready_to_add;
    if (!ready || (!ready.external_id && !ready.name)) return;
    setRepricing(true);
    try {
      const { data } = await api.scanReprice({
        external_id: ready.external_id || '',
        name: ready.name,
        set_name: ready.set_name,
        rarity: newRarity,
      });
      setPrice(data?.tcgplayer_price ?? null);
      setPriceSource(data?.source ?? null);
    } catch {
      // Non-fatal: keep the previous price preview; the server re-fetches on add.
    } finally {
      setRepricing(false);
    }
  };

  const addToCollection = async () => {
    const ready = result?.ready_to_add;
    if (!ready) return;
    const nonFoil = ['common', 'rare'].includes((chosenRarity || '').toLowerCase());
    const payload = {
      ...ready,
      rarity: chosenRarity || ready.rarity || null,
      is_foil: chosenRarity ? !nonFoil : ready.is_foil,
    };
    setSaveState('saving');
    setSaveMsg(null);
    try {
      const { data } = await api.createCard(payload);
      if (data?.merged) {
        setSaveState('merged');
        setSaveMsg(`Already had it — bumped quantity to ${data.quantity}.`);
      } else {
        setSaveState('saved');
        setSaveMsg(`Added "${data.name}" (${data.rarity || 'no rarity'}).`);
      }
    } catch (err) {
      setSaveState('error');
      setSaveMsg(err?.response?.data?.detail || err?.message || 'Add failed');
    }
  };

  const r = result?.rarity;
  const c = result?.candidate;
  const lowConfidence = r && r.confidence < LOW_CONFIDENCE;

  return (
    <section className="scan-page identify-page">
      <h2>Scan a Card</h2>
      <p className="muted">
        Hold a Yu-Gi-Oh! card in front of the Pi camera and frame it in the box
        below. Press <strong>Capture</strong>, then <strong>slowly tilt the
        card</strong> for ~2 seconds so the foil catches the light — that's how
        the rarity is read. Review the result and add it to your collection.
      </p>

      {/* Live preview */}
      <div className="scan-preview" style={{ position: 'relative', maxWidth: 520 }}>
        {!previewBroken ? (
          <img
            src={previewSrc}
            alt="Live camera preview"
            onError={() => setPreviewBroken(true)}
            style={{ width: '100%', borderRadius: 8, display: 'block', background: '#111' }}
          />
        ) : (
          <div className="error" style={{ padding: 16 }}>
            Camera preview unavailable. The scanner needs the Pi camera
            (picamera2) — or set <code>SCAN_FAKE_CAMERA_DIR</code> for testing.
            <div style={{ marginTop: 8 }}>
              <button type="button" className="ghost"
                onClick={() => { setPreviewBroken(false); setPreviewNonce((n) => n + 1); }}>
                Retry preview
              </button>
            </div>
          </div>
        )}
        {/* Framing guide overlay */}
        {!previewBroken && (
          <div
            aria-hidden
            style={{
              position: 'absolute', inset: '8% 22%', border: '2px dashed rgba(255,255,255,.6)',
              borderRadius: 10, pointerEvents: 'none',
            }}
          />
        )}
      </div>

      <div style={{ marginTop: 12 }}>
        <button type="button" className="primary" onClick={capture}
          disabled={phase === 'capturing'}>
          {phase === 'capturing' ? 'Capturing & reading foil…' : 'Capture'}
        </button>
      </div>

      {phase === 'error' && <div className="error" style={{ marginTop: 12 }}>{error}</div>}

      {/* Result */}
      {phase === 'done' && result && (
        <div className="scan-result identify-result-card" data-game="yugioh"
          style={{ marginTop: 16 }}>
          {result.error && !c && <div className="error">{result.error}</div>}

          {c && (
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
              {result.ready_to_add?.image_url && (
                <img src={result.ready_to_add.image_url} alt={c.name}
                  style={{ width: 140, borderRadius: 6, alignSelf: 'flex-start' }} />
              )}
              <div style={{ flex: 1, minWidth: 240 }}>
                <div className="candidate-meta">
                  <span className="candidate-name" style={{ fontSize: '1.1rem' }}>{c.name}</span>
                  <span className="candidate-conf">{fmtPct(c.confidence)} card match</span>
                </div>
                {c.set_name && <div className="candidate-set">{c.set_name}</div>}

                {/* Rarity */}
                <div style={{ marginTop: 12 }}>
                  <label className="muted" style={{ display: 'block', marginBottom: 4 }}>
                    Detected rarity{r ? ` · ${fmtPct(r.confidence)} confidence · ${r.method}` : ''}
                  </label>
                  <select value={chosenRarity} onChange={(e) => onChangeRarity(e.target.value)}>
                    <option value="">(unknown / not set)</option>
                    {RARITY_VOCAB.map((opt) => (
                      <option key={opt} value={opt}>{opt}</option>
                    ))}
                  </select>
                  {r?.reasoning && (
                    <div className="candidate-just muted" style={{ marginTop: 6 }}>"{r.reasoning}"</div>
                  )}
                  {r?.alternatives?.length > 0 && (
                    <div className="muted" style={{ marginTop: 6 }}>
                      Or:&nbsp;
                      {r.alternatives.map((alt) => (
                        <button key={alt} type="button" className="ghost"
                          style={{ marginRight: 6 }} onClick={() => onChangeRarity(alt)}>
                          {alt}
                        </button>
                      ))}
                    </div>
                  )}
                  {lowConfidence && (
                    <div className="muted" style={{ marginTop: 6, color: '#c47f00' }}>
                      ⚠ Low confidence — double-check the rarity before adding.
                    </div>
                  )}
                </div>

                {/* Price */}
                <div style={{ marginTop: 12 }}>
                  <strong>Price:</strong>{' '}
                  {repricing ? 'updating…' : fmtPrice(price)}
                  {priceSource && !repricing && (
                    <span className="muted"> &nbsp;({priceSource})</span>
                  )}
                </div>

                {result.error && (
                  <div className="muted" style={{ marginTop: 8 }}>{result.error}</div>
                )}

                {/* Add */}
                <div style={{ marginTop: 16 }}>
                  <button type="button" className="primary" onClick={addToCollection}
                    disabled={saveState === 'saving'}>
                    {saveState === 'saving' ? 'Adding…' : 'Add to collection'}
                  </button>
                  <button type="button" className="ghost" style={{ marginLeft: 8 }}
                    onClick={capture}>
                    Rescan
                  </button>
                </div>
                {saveMsg && (
                  <div className={saveState === 'error' ? 'error' : 'muted'}
                    style={{ marginTop: 8 }}>
                    {saveMsg}
                    {(saveState === 'saved' || saveState === 'merged') && (
                      <button type="button" className="ghost" style={{ marginLeft: 8 }}
                        onClick={() => navigate('/cards')}>
                        View collection
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
};

export default ScanPage;
