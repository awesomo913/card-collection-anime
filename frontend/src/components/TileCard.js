import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';
import Sparkline from './Sparkline';

const SOURCE_COLORS = {
  TCGPlayer:  'var(--neon-cyan)',
  eBay:       'var(--neon-gold)',
  CardMarket: 'var(--neon-pink)',
};

const TileCard = ({ item, onDelete }) => {
  const isSealed = !!item.product_type;
  const itemType = isSealed ? 'sealed' : 'card';
  const editPath = isSealed ? `/sealed/edit/${item.id}` : `/cards/edit/${item.id}`;
  const game = (item.game || '').toLowerCase();
  const price = item.current_price;
  const priceSources = item.price_sources || {};
  const maxSourcePrice = Math.max(...Object.values(priceSources), 0.01);

  const [history, setHistory] = useState(null);

  useEffect(() => {
    let mounted = true;
    api.getPriceHistory(itemType, item.id)
      .then((res) => {
        if (!mounted) return;
        // Average prices per timestamp into a single sparkline series.
        const byTs = {};
        for (const row of res.data || []) {
          const ts = row.timestamp;
          if (!byTs[ts]) byTs[ts] = [];
          byTs[ts].push(row.price);
        }
        const series = Object.keys(byTs)
          .sort()
          .map((ts) => byTs[ts].reduce((a, b) => a + b, 0) / byTs[ts].length);
        setHistory(series);
      })
      .catch(() => setHistory([]));
    return () => { mounted = false; };
  }, [itemType, item.id]);

  return (
    <article className="anime-tile" data-game={game} aria-label={item.name}>
      <header className="tile-header">
        <div className="tile-title">{item.name}</div>
        <div className="tile-subtitle">
          {item.set_name || '—'} • {item.game || '?'}
          {item.is_foil ? ' • FOIL' : ''}
          {isSealed ? ` • ${item.product_type}` : ''}
        </div>
      </header>

      <div className="tile-price">
        <span className="label">Current</span>
        <span className="value">{price != null ? `$${price.toFixed(2)}` : 'N/A'}</span>
      </div>

      {Object.keys(priceSources).length > 0 && (
        <div className="price-sources">
          {Object.entries(priceSources).map(([src, p]) => {
            const widthPct = Math.max(6, Math.min(100, (p / maxSourcePrice) * 100));
            return (
              <div className="source-bar" key={src} data-source={src}>
                <span className="src-name">{src}</span>
                <div className="bar" style={{ width: `${widthPct}%` }} aria-hidden="true" />
                <span className="src-price">${(typeof p === 'number' ? p : 0).toFixed(2)}</span>
              </div>
            );
          })}
        </div>
      )}

      {history && history.length >= 2 && (
        <div className="sparkline-wrap">
          <div className="label">7d Trend</div>
          <Sparkline points={history.slice(-30)} stroke={SOURCE_COLORS.TCGPlayer} />
        </div>
      )}

      <footer className="tile-actions">
        <Link to={editPath}>Edit</Link>
        <button className="ghost" onClick={() => onDelete(item.id)}>Delete</button>
      </footer>
    </article>
  );
};

export default TileCard;
