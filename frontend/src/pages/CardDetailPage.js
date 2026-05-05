import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import api from '../services/api';
import Sparkline from '../components/Sparkline';

/* --------------------------------------------------------------------------
 * CardDetailPage — read-only deep-dive on a single card.
 *
 * Phase D addition: previously the only way to interact with a saved card was
 * the Edit form. This page gives a clean read-only view with a larger
 * sparkline, full price-history table, and quick Edit/Delete buttons. Linked
 * from TileCard click in the list.
 *
 * Route: /cards/:id  (edit form lives at /cards/:id/edit and /cards/edit/:id
 * for backwards compatibility).
 * ------------------------------------------------------------------------ */

const SOURCE_COLORS = {
  TCGPlayer:  'var(--neon-cyan)',
  eBay:       'var(--neon-gold)',
  CardMarket: 'var(--neon-pink)',
};

const formatTs = (iso) => {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
};

const CardDetailPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [card, setCard] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    Promise.all([
      api.getCard(id),
      api.getPriceHistory('card', id),
    ])
      .then(([cardRes, histRes]) => {
        if (!mounted) return;
        setCard(cardRes.data);
        setHistory(histRes.data || []);
      })
      .catch((err) => {
        if (!mounted) return;
        console.error(err);
        setError(err?.response?.status === 404 ? 'Card not found.' : 'Failed to load card.');
      })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, [id]);

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
  if (error) return (
    <section>
      <div className="error">{error}</div>
      <Link to="/cards">← Back to My Cards</Link>
    </section>
  );
  if (!card) return null;

  // Aggregate price history into a single series (averaged per timestamp)
  // for the headline sparkline. Per-source data shown in the table below.
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
          <Link to={`/cards/${id}/edit`} className="primary">Edit</Link>
          <button type="button" className="ghost" onClick={handleDelete}>Delete</button>
        </div>
      </div>

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
          <div className="meta-row">
            <span className="label">Set</span>
            <span>{card.set_name || '—'}</span>
          </div>
          <div className="meta-row">
            <span className="label">Game</span>
            <span>{card.game || '—'}</span>
          </div>
          <div className="meta-row">
            <span className="label">Rarity</span>
            <span>{card.rarity || '—'}</span>
          </div>
          <div className="meta-row">
            <span className="label">Condition</span>
            <span>{card.condition || '—'}</span>
          </div>
          <div className="meta-row">
            <span className="label">Quantity</span>
            <span>{card.quantity ?? 1}</span>
          </div>
          {card.is_foil && <div className="meta-row"><span className="label">Foil</span><span>Yes</span></div>}
          {card.is_signed && <div className="meta-row"><span className="label">Signed</span><span>Yes</span></div>}
          {card.purchase_price != null && (
            <div className="meta-row">
              <span className="label">Bought for</span>
              <span>${card.purchase_price.toFixed(2)}</span>
            </div>
          )}
          <div className="meta-row big">
            <span className="label">Current value</span>
            <span className="value">
              {card.current_price != null ? `$${card.current_price.toFixed(2)}` : 'N/A'}
            </span>
          </div>
          {card.notes && (
            <div className="meta-row notes">
              <span className="label">Notes</span>
              <span>{card.notes}</span>
            </div>
          )}
          {card.external_source && card.external_id && (
            <div className="meta-row">
              <span className="label">Pinned</span>
              <span className="linked-badge">
                {card.external_source}:{card.external_id}
              </span>
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
            <thead>
              <tr><th>Source</th><th>Price</th></tr>
            </thead>
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
            <thead>
              <tr><th>When</th><th>Source</th><th>Price</th></tr>
            </thead>
            <tbody>
              {history.slice().reverse().slice(0, 50).map((row, i) => (
                <tr key={i}>
                  <td>{formatTs(row.timestamp)}</td>
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
