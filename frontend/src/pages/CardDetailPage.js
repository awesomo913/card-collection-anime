import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import api from '../services/api';
import Sparkline from '../components/Sparkline';
import { CONDITIONS } from '../data/options';

/* --------------------------------------------------------------------------
 * CardDetailPage — read-mostly view with inline edit affordances.
 *
 * Editable inline (no separate Edit form needed for these):
 *   - quantity   (+/- buttons + number input)
 *   - notes      (textarea, saves on blur)
 *   - purchase_price  (what you paid, click to edit)
 *   - acquired_price  (what the market said when you added it; user can override
 *                      if their snapshot didn't capture correctly)
 *   - condition  (dropdown, saves on change)
 *
 * Click "Edit full form" to open the legacy form for fields that don't
 * benefit from inline editing (name, set, rarity, image_url, etc).
 *
 * Route: /cards/:id  (edit form at /cards/:id/edit)
 * ------------------------------------------------------------------------ */

const SOURCE_COLORS = {
  TCGPlayer:  'var(--neon-cyan)',
  eBay:       'var(--neon-gold)',
  CardMarket: 'var(--neon-pink)',
};

const fmtTs = (iso) => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
};

const fmtDate = (iso) => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
  }); } catch { return iso; }
};

const fmtMoney = (n) => (n == null ? '—' : `$${n.toFixed(2)}`);

const CardDetailPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [card, setCard] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [saveStatus, setSaveStatus] = useState(null);  // null | 'saving' | 'saved' | 'error'
  // Local edit-mirror state for fields that aren't inline-instant (notes,
  // purchase_price, acquired_price). Updates are flushed on blur or Save.
  const [draftNotes, setDraftNotes] = useState('');
  const [draftPurchase, setDraftPurchase] = useState('');
  const [draftAcquired, setDraftAcquired] = useState('');

  const loadCard = () => Promise.all([
    api.getCard(id),
    api.getPriceHistory('card', id),
  ]).then(([cardRes, histRes]) => {
    setCard(cardRes.data);
    setHistory(histRes.data || []);
    setDraftNotes(cardRes.data.notes || '');
    setDraftPurchase(cardRes.data.purchase_price ?? '');
    setDraftAcquired(cardRes.data.acquired_price ?? '');
  });

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    loadCard()
      .catch((err) => {
        if (!mounted) return;
        console.error(err);
        setError(err?.response?.status === 404 ? 'Card not found.' : 'Failed to load card.');
      })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  /**
   * Push a partial update to the API. Optimistically merges into local state
   * so the UI updates instantly; reverts on failure. Returns the promise so
   * callers can await it if they need to chain.
   */
  const saveField = async (patch) => {
    if (!card) return;
    const previous = { ...card };
    setSaveStatus('saving');
    setCard((prev) => ({ ...prev, ...patch }));
    try {
      const res = await api.updateCard(id, patch);
      setCard(res.data);
      setSaveStatus('saved');
      setTimeout(() => setSaveStatus(null), 1500);
    } catch (err) {
      console.error('Inline save failed:', err);
      setCard(previous);  // revert optimistic update
      setSaveStatus('error');
      setError(err?.response?.data?.detail || 'Save failed.');
      setTimeout(() => setSaveStatus(null), 3000);
    }
  };

  const bumpQty = (delta) => {
    const cur = Number.isFinite(card?.quantity) ? card.quantity : 1;
    const next = Math.max(1, cur + delta);
    if (next === cur) return;
    saveField({ quantity: next });
  };

  const handleDelete = async () => {
    if (!window.confirm(`Delete "${card?.name || 'this card'}"? This cannot be undone.`)) return;
    try {
      await api.deleteCard(id);
      navigate('/cards');
    } catch (err) {
      console.error(err);
      setError('Failed to delete card.');
    }
  };

  if (loading) return <div className="loading">Loading…</div>;
  if (error && !card) return (
    <section>
      <div className="error">{error}</div>
      <Link to="/cards">← Back to My Cards</Link>
    </section>
  );
  if (!card) return null;

  const qty = Number.isFinite(card.quantity) ? card.quantity : 1;
  const currentPrice = card.current_price;
  const lineTotal = currentPrice != null ? currentPrice * qty : null;
  // Gain/loss math: per-card and total. Null when either side is missing.
  const acquired = card.acquired_price;
  const gainPerCard = (currentPrice != null && acquired != null) ? currentPrice - acquired : null;
  const gainTotal = (gainPerCard != null) ? gainPerCard * qty : null;
  const gainPct = (gainPerCard != null && acquired) ? (gainPerCard / acquired) * 100 : null;

  // Aggregate price history into a single series (averaged per timestamp).
  const byTs = {};
  for (const row of history) {
    if (!byTs[row.timestamp]) byTs[row.timestamp] = [];
    byTs[row.timestamp].push(row.price);
  }
  const series = Object.keys(byTs).sort().map(
    (ts) => byTs[ts].reduce((a, b) => a + b, 0) / byTs[ts].length
  );

  return (
    <section className="card-detail-page" data-game={(card.game || '').toLowerCase()}>
      <div className="detail-header">
        <Link to="/cards" className="back-link">← Back to My Cards</Link>
        <div className="detail-actions">
          {saveStatus === 'saving' && <span className="save-indicator">Saving…</span>}
          {saveStatus === 'saved' && <span className="save-indicator saved">✓ Saved</span>}
          {saveStatus === 'error' && <span className="save-indicator error">✗ Failed</span>}
          <Link to={`/cards/${id}/edit`} className="primary">Edit full form</Link>
          <button type="button" className="ghost" onClick={handleDelete}>Delete</button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <article className="detail-card">
        <div className="detail-image">
          {card.image_url ? (
            <img src={card.image_url} alt={card.name} />
          ) : (
            <div className="no-image">No image</div>
          )}
        </div>

        <div className="detail-meta">
          <h2>{card.name}</h2>
          <div className="meta-row"><span className="label">Set</span><span>{card.set_name || '—'}</span></div>
          <div className="meta-row"><span className="label">Game</span><span>{card.game || '—'}</span></div>
          <div className="meta-row"><span className="label">Rarity</span><span>{card.rarity || '—'}</span></div>

          {/* Condition: inline dropdown, saves on change */}
          <div className="meta-row">
            <span className="label">Condition</span>
            <span>
              <input
                type="text"
                list="detail-condition-options"
                className="inline-input"
                value={card.condition || ''}
                onChange={(e) => setCard((p) => ({ ...p, condition: e.target.value }))}
                onBlur={(e) => {
                  if (e.target.value !== (card.condition || '')) {
                    saveField({ condition: e.target.value || null });
                  }
                }}
                placeholder="—"
              />
              <datalist id="detail-condition-options">
                {CONDITIONS.map((c) => <option key={c} value={c} />)}
              </datalist>
            </span>
          </div>

          {/* Quantity: -/+ buttons + number input. Saves immediately on click. */}
          <div className="meta-row qty-row">
            <span className="label">Quantity</span>
            <span className="qty-stepper">
              <button
                type="button"
                className="qty-btn"
                onClick={() => bumpQty(-1)}
                disabled={qty <= 1}
                aria-label="Decrease quantity"
              >−</button>
              <input
                type="number"
                min="1"
                className="qty-input"
                value={qty}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  if (Number.isFinite(n) && n >= 1) {
                    setCard((p) => ({ ...p, quantity: n }));
                  }
                }}
                onBlur={(e) => {
                  const n = parseInt(e.target.value, 10) || 1;
                  if (n !== (card.quantity || 1)) saveField({ quantity: n });
                }}
                aria-label="Quantity"
              />
              <button
                type="button"
                className="qty-btn"
                onClick={() => bumpQty(1)}
                aria-label="Increase quantity"
              >+</button>
            </span>
          </div>

          {card.is_foil && <div className="meta-row"><span className="label">Foil</span><span>Yes</span></div>}
          {card.is_signed && <div className="meta-row"><span className="label">Signed</span><span>Yes</span></div>}

          {/* Acquired snapshot: editable but rarely changed. */}
          <div className="meta-row">
            <span className="label">Added on</span>
            <span>{fmtDate(card.created_at || card.last_updated)}</span>
          </div>
          <div className="meta-row inline-editable">
            <span className="label">Initial market</span>
            <span>
              <span className="dollar-prefix">$</span>
              <input
                type="number"
                step="0.01"
                min="0"
                className="inline-input money-input"
                value={draftAcquired}
                onChange={(e) => setDraftAcquired(e.target.value)}
                onBlur={() => {
                  const n = draftAcquired === '' ? null : parseFloat(draftAcquired);
                  if (n !== card.acquired_price) saveField({ acquired_price: n });
                }}
                placeholder="—"
              />
            </span>
          </div>
          <div className="meta-row inline-editable">
            <span className="label">Bought for</span>
            <span>
              <span className="dollar-prefix">$</span>
              <input
                type="number"
                step="0.01"
                min="0"
                className="inline-input money-input"
                value={draftPurchase}
                onChange={(e) => setDraftPurchase(e.target.value)}
                onBlur={() => {
                  const n = draftPurchase === '' ? null : parseFloat(draftPurchase);
                  if (n !== card.purchase_price) saveField({ purchase_price: n });
                }}
                placeholder="—"
              />
            </span>
          </div>

          {/* Current price + line total */}
          <div className="meta-row big">
            <span className="label">{qty > 1 ? 'Per card' : 'Current value'}</span>
            <span className="value">{fmtMoney(currentPrice)}</span>
          </div>
          {qty > 1 && lineTotal != null && (
            <div className="meta-row big">
              <span className="label">×{qty} total</span>
              <span className="value">{fmtMoney(lineTotal)}</span>
            </div>
          )}

          {/* Gain/loss since acquisition */}
          {gainPerCard != null && (
            <div className={`meta-row big gain ${gainPerCard >= 0 ? 'positive' : 'negative'}`}>
              <span className="label">Since added</span>
              <span className="value">
                {gainPerCard >= 0 ? '+' : ''}{fmtMoney(gainPerCard).replace('$-', '−$')}
                {qty > 1 && gainTotal != null && (
                  <span className="gain-total">
                    &nbsp;(×{qty} = {gainTotal >= 0 ? '+' : ''}{fmtMoney(gainTotal).replace('$-', '−$')})
                  </span>
                )}
                {gainPct != null && (
                  <span className="gain-pct">
                    &nbsp;{gainPct >= 0 ? '+' : ''}{gainPct.toFixed(1)}%
                  </span>
                )}
              </span>
            </div>
          )}

          {/* Notes: inline textarea, saves on blur */}
          <div className="meta-row notes inline-editable">
            <span className="label">Notes</span>
            <textarea
              className="inline-textarea"
              value={draftNotes}
              onChange={(e) => setDraftNotes(e.target.value)}
              onBlur={() => {
                if ((draftNotes || '') !== (card.notes || '')) {
                  saveField({ notes: draftNotes || null });
                }
              }}
              placeholder="Add notes (binder slot, deck list, sentimental value, etc.)"
              rows={2}
            />
          </div>

          {card.external_source && card.external_id && (
            <div className="meta-row">
              <span className="label">Pinned</span>
              <span className="linked-badge">{card.external_source}:{card.external_id}</span>
            </div>
          )}
        </div>
      </article>

      {series.length >= 2 && (
        <div className="detail-sparkline">
          <h3>Price trend</h3>
          <Sparkline points={series} stroke={SOURCE_COLORS.TCGPlayer} />
        </div>
      )}

      {card.price_sources && Object.keys(card.price_sources).length > 0 && (
        <div className="detail-sources">
          <h3>Latest by source</h3>
          <table>
            <thead><tr><th>Source</th><th>Price</th></tr></thead>
            <tbody>
              {Object.entries(card.price_sources).map(([src, p]) => (
                <tr key={src}>
                  <td>{src}</td>
                  <td>${(typeof p === 'number' ? p : 0).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {history.length > 0 && (
        <div className="detail-history">
          <h3>Price history ({history.length} samples)</h3>
          <table>
            <thead><tr><th>When</th><th>Source</th><th>Price</th></tr></thead>
            <tbody>
              {history.slice().reverse().slice(0, 50).map((row, i) => (
                <tr key={i}>
                  <td>{fmtTs(row.timestamp)}</td>
                  <td>{row.source}</td>
                  <td>${row.price.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {history.length > 50 && (
            <p className="muted">Showing newest 50 of {history.length} samples.</p>
          )}
        </div>
      )}
    </section>
  );
};

export default CardDetailPage;
