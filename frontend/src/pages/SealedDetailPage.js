import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import api from '../services/api';
import Sparkline from '../components/Sparkline';
import { PRODUCT_TYPES_BY_GAME } from '../data/options';

/* --------------------------------------------------------------------------
 * SealedDetailPage — read-mostly view with inline edit for sealed products.
 *
 * Sibling to CardDetailPage. Same UX shape so muscle memory transfers:
 * inline qty stepper, notes editor, "Added on" + "Initial market" snapshot,
 * gain/loss "Since added", price-trend sparkline, latest-by-source table,
 * full price history table.
 *
 * Differences from CardDetailPage:
 *   - No condition, rarity, foil, signed, card_number (sealed don't have them).
 *   - Has a product_type field (Booster Box / Tin / Elite Trainer Box / etc).
 *
 * Route: /sealed/:id  (edit form at /sealed/:id/edit)
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

const SealedDetailPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [item, setItem] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [saveStatus, setSaveStatus] = useState(null);
  const [draftNotes, setDraftNotes] = useState('');
  const [draftPurchase, setDraftPurchase] = useState('');
  const [draftAcquired, setDraftAcquired] = useState('');

  const loadItem = () => Promise.all([
    api.getSealedProduct(id),
    api.getPriceHistory('sealed', id),
  ]).then(([itemRes, histRes]) => {
    setItem(itemRes.data);
    setHistory(histRes.data || []);
    setDraftNotes(itemRes.data.notes || '');
    setDraftPurchase(itemRes.data.purchase_price ?? '');
    setDraftAcquired(itemRes.data.acquired_price ?? '');
  });

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    loadItem()
      .catch((err) => {
        if (!mounted) return;
        console.error(err);
        setError(err?.response?.status === 404 ? 'Product not found.' : 'Failed to load product.');
      })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  /**
   * Optimistic PUT. Same pattern as CardDetailPage.saveField — reverts on
   * failure so the UI stays consistent without round-trip wait.
   */
  const saveField = async (patch) => {
    if (!item) return;
    const previous = { ...item };
    setSaveStatus('saving');
    setItem((prev) => ({ ...prev, ...patch }));
    try {
      const res = await api.updateSealedProduct(id, patch);
      setItem(res.data);
      setSaveStatus('saved');
      setTimeout(() => setSaveStatus(null), 1500);
    } catch (err) {
      console.error('Inline save failed:', err);
      setItem(previous);
      setSaveStatus('error');
      setError(err?.response?.data?.detail || 'Save failed.');
      setTimeout(() => setSaveStatus(null), 3000);
    }
  };

  const bumpQty = (delta) => {
    const cur = Number.isFinite(item?.quantity) ? item.quantity : 1;
    const next = Math.max(1, cur + delta);
    if (next === cur) return;
    saveField({ quantity: next });
  };

  const handleDelete = async () => {
    if (!window.confirm(`Delete "${item?.name || 'this product'}"? This cannot be undone.`)) return;
    try {
      await api.deleteSealedProduct(id);
      navigate('/sealed');
    } catch (err) {
      console.error(err);
      setError('Failed to delete product.');
    }
  };

  if (loading) return <div className="loading">Loading…</div>;
  if (error && !item) return (
    <section>
      <div className="error">{error}</div>
      <Link to="/sealed">← Back to Sealed Products</Link>
    </section>
  );
  if (!item) return null;

  const qty = Number.isFinite(item.quantity) ? item.quantity : 1;
  const currentPrice = item.current_price;
  const lineTotal = currentPrice != null ? currentPrice * qty : null;
  const acquired = item.acquired_price;
  const gainPerUnit = (currentPrice != null && acquired != null) ? currentPrice - acquired : null;
  const gainTotal = (gainPerUnit != null) ? gainPerUnit * qty : null;
  const gainPct = (gainPerUnit != null && acquired) ? (gainPerUnit / acquired) * 100 : null;

  const byTs = {};
  for (const row of history) {
    if (!byTs[row.timestamp]) byTs[row.timestamp] = [];
    byTs[row.timestamp].push(row.price);
  }
  const series = Object.keys(byTs).sort().map(
    (ts) => byTs[ts].reduce((a, b) => a + b, 0) / byTs[ts].length
  );

  const productTypes = PRODUCT_TYPES_BY_GAME[item.game] || [];

  return (
    <section className="card-detail-page" data-game={(item.game || '').toLowerCase()}>
      <div className="detail-header">
        <Link to="/sealed" className="back-link">← Back to Sealed Products</Link>
        <div className="detail-actions">
          {saveStatus === 'saving' && <span className="save-indicator">Saving…</span>}
          {saveStatus === 'saved' && <span className="save-indicator saved">✓ Saved</span>}
          {saveStatus === 'error' && <span className="save-indicator error">✗ Failed</span>}
          <Link to={`/sealed/${id}/edit`} className="primary">Edit full form</Link>
          <button type="button" className="ghost" onClick={handleDelete}>Delete</button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <article className="detail-card">
        <div className="detail-image">
          {item.image_url ? (
            <img src={item.image_url} alt={item.name} />
          ) : (
            <div className="no-image">No image</div>
          )}
        </div>

        <div className="detail-meta">
          <h2>{item.name}</h2>
          <div className="meta-row"><span className="label">Set</span><span>{item.set_name || '—'}</span></div>
          <div className="meta-row"><span className="label">Game</span><span>{item.game || '—'}</span></div>

          {/* Product type: inline text input + datalist, saves on blur. */}
          <div className="meta-row">
            <span className="label">Type</span>
            <span>
              <input
                type="text"
                list="detail-product-type-options"
                className="inline-input"
                value={item.product_type || ''}
                onChange={(e) => setItem((p) => ({ ...p, product_type: e.target.value }))}
                onBlur={(e) => {
                  if (e.target.value !== (item.product_type || '')) {
                    saveField({ product_type: e.target.value || null });
                  }
                }}
                placeholder="—"
              />
              <datalist id="detail-product-type-options">
                {productTypes.map((t) => <option key={t} value={t} />)}
              </datalist>
            </span>
          </div>

          {/* Quantity stepper — same UX as CardDetailPage. */}
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
                    setItem((p) => ({ ...p, quantity: n }));
                  }
                }}
                onBlur={(e) => {
                  const n = parseInt(e.target.value, 10) || 1;
                  if (n !== (item.quantity || 1)) saveField({ quantity: n });
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

          <div className="meta-row">
            <span className="label">Added on</span>
            <span>{fmtDate(item.created_at || item.last_updated)}</span>
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
                  if (n !== item.acquired_price) saveField({ acquired_price: n });
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
                  if (n !== item.purchase_price) saveField({ purchase_price: n });
                }}
                placeholder="—"
              />
            </span>
          </div>

          <div className="meta-row big">
            <span className="label">{qty > 1 ? 'Per unit' : 'Current value'}</span>
            <span className="value">{fmtMoney(currentPrice)}</span>
          </div>
          {qty > 1 && lineTotal != null && (
            <div className="meta-row big">
              <span className="label">×{qty} total</span>
              <span className="value">{fmtMoney(lineTotal)}</span>
            </div>
          )}

          {gainPerUnit != null && (
            <div className={`meta-row big gain ${gainPerUnit >= 0 ? 'positive' : 'negative'}`}>
              <span className="label">Since added</span>
              <span className="value">
                {gainPerUnit >= 0 ? '+' : ''}{fmtMoney(gainPerUnit).replace('$-', '−$')}
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

          <div className="meta-row notes inline-editable">
            <span className="label">Notes</span>
            <textarea
              className="inline-textarea"
              value={draftNotes}
              onChange={(e) => setDraftNotes(e.target.value)}
              onBlur={() => {
                if ((draftNotes || '') !== (item.notes || '')) {
                  saveField({ notes: draftNotes || null });
                }
              }}
              placeholder="Add notes (storage location, intended use, sentimental value, etc.)"
              rows={2}
            />
          </div>

          {item.external_source && item.external_id && (
            <div className="meta-row">
              <span className="label">Pinned</span>
              <span className="linked-badge">{item.external_source}:{item.external_id}</span>
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

      {item.price_sources && Object.keys(item.price_sources).length > 0 && (
        <div className="detail-sources">
          <h3>Latest by source</h3>
          <table>
            <thead><tr><th>Source</th><th>Price</th></tr></thead>
            <tbody>
              {Object.entries(item.price_sources).map(([src, p]) => (
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

export default SealedDetailPage;
