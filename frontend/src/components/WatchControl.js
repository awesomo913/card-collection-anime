import React, { useEffect, useState } from 'react';
import api from '../services/api';

/**
 * WatchControl — add / edit / remove a price-alert watch for one item.
 *
 * Mounts beside ForecastPanel on the card and sealed detail pages. Pulls the
 * current watchlist on mount to show whether this item is already watched, and
 * lets the user pick a direction (drop / rise / either) + a percent threshold.
 * Baseline defaults to the item's current price (server-side) when left blank.
 *
 * Props:
 *   itemType      'card' | 'sealed'
 *   itemId        numeric id
 *   currentPrice  optional — shown as the baseline hint
 */
const DIRECTIONS = [
  { value: 'drop', label: '📉 Drops by' },
  { value: 'rise', label: '📈 Rises by' },
  { value: 'either', label: '🔀 Moves by' },
];

const WatchControl = ({ itemType, itemId, currentPrice }) => {
  const [watch, setWatch] = useState(null);
  const [direction, setDirection] = useState('drop');
  const [threshold, setThreshold] = useState(10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let mounted = true;
    api.getWatchlist()
      .then((res) => {
        if (!mounted) return;
        const mine = res.data.find(
          (w) => w.item_type === itemType && w.item_id === itemId
        );
        if (mine) {
          setWatch(mine);
          setDirection(mine.direction);
          setThreshold(mine.threshold_pct);
        }
      })
      .catch(() => {/* watchlist is non-critical UI; stay silent on load */});
    return () => { mounted = false; };
  }, [itemType, itemId]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.addWatch({
        item_type: itemType,
        item_id: itemId,
        direction,
        threshold_pct: Number(threshold),
      });
      setWatch(res.data);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Could not save watch.');
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!watch) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteWatch(watch.id);
      setWatch(null);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Could not remove watch.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="watch-control">
      <div className="watch-control-header">
        <h3>🔔 Price alert</h3>
        {watch && <span className="watch-badge">Watching</span>}
      </div>

      <div className="watch-control-row">
        <span className="watch-when">Alert when price</span>
        <select
          value={direction}
          onChange={(e) => setDirection(e.target.value)}
          className="watch-select"
          disabled={busy}
        >
          {DIRECTIONS.map((d) => (
            <option key={d.value} value={d.value}>{d.label}</option>
          ))}
        </select>
        <input
          type="number"
          min="1"
          step="1"
          value={threshold}
          onChange={(e) => setThreshold(e.target.value)}
          className="watch-input"
          disabled={busy}
        />
        <span className="watch-pct">%</span>
      </div>

      {currentPrice != null && (
        <p className="watch-hint">
          Baseline: ${Number(currentPrice).toFixed(2)} (current price)
        </p>
      )}

      <div className="watch-actions">
        <button className="watch-save" onClick={save} disabled={busy}>
          {watch ? 'Update watch' : 'Watch this card'}
        </button>
        {watch && (
          <button className="watch-remove" onClick={remove} disabled={busy}>
            Remove
          </button>
        )}
      </div>

      {error && <p className="watch-error">{error}</p>}
    </div>
  );
};

export default WatchControl;
