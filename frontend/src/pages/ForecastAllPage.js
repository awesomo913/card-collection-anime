import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';

/**
 * ForecastAllPage — whole-collection forecast view.
 *
 * Loads cards + sealed, POSTs them all to /forecast/batch, then renders:
 *   1. Aggregate banner at top — three horizons (7/30/90d) with portfolio
 *      low/target/high + a confidence-weighted target.
 *   2. Per-item grid below — each row shows direction, target, confidence
 *      pill, and a hover tooltip with caveats when present.
 *
 * Cold-cache cost: 5-15s per item × N items, fanned out 4-wide on the Pi.
 * Re-runs while history unchanged are ~free (server cache fires before
 * any DeepSeek call). Manual trigger only — no auto-run.
 */

const horizonLabel = (days) => ({ 7: '7-day', 30: '30-day', 90: '90-day' }[days] || `${days}d`);

const fmtMoney = (n) => (typeof n === 'number' ? `$${n.toFixed(2)}` : '—');

const fmtDelta = (current, projected) => {
  if (typeof current !== 'number' || typeof projected !== 'number') return null;
  if (current === 0) return null;
  const pct = ((projected - current) / current) * 100;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}%`;
};

const confidenceClass = (conf) => {
  if (conf >= 0.6) return 'conf-high';
  if (conf >= 0.3) return 'conf-mid';
  return 'conf-low';
};

const directionArrow = (direction) => ({
  up: '↑', down: '↓', flat: '→', unknown: '?',
}[direction] || '?');

const ForecastAllPage = () => {
  const [phase, setPhase] = useState('loading');  // loading | running | done | error
  const [itemCount, setItemCount] = useState(0);
  const [response, setResponse] = useState(null);
  const [error, setError] = useState(null);
  const [elapsedSec, setElapsedSec] = useState(0);

  useEffect(() => {
    let mounted = true;
    let tickHandle = null;
    const started = Date.now();
    const tick = () => {
      if (!mounted) return;
      setElapsedSec(Math.round((Date.now() - started) / 1000));
      tickHandle = setTimeout(tick, 1000);
    };

    (async () => {
      try {
        const [cardsRes, sealedRes] = await Promise.all([
          api.getCards(), api.getSealedProducts(),
        ]);
        if (!mounted) return;
        const items = [
          ...cardsRes.data.map((c) => ({ type: 'card', id: c.id })),
          ...sealedRes.data.map((s) => ({ type: 'sealed', id: s.id })),
        ];
        setItemCount(items.length);
        if (items.length === 0) {
          setPhase('done');
          setResponse({ results: [], aggregate: [], duration_seconds: 0,
                        cache_hits: 0, cache_misses: 0, model: '(none)' });
          return;
        }
        setPhase('running');
        tick();
        const res = await api.forecastBatch(items);
        if (!mounted) return;
        setResponse(res.data);
        setPhase('done');
      } catch (e) {
        if (!mounted) return;
        setError(e?.response?.data?.detail || e?.message || 'Forecast failed');
        setPhase('error');
      } finally {
        if (tickHandle) clearTimeout(tickHandle);
      }
    })();

    return () => { mounted = false; if (tickHandle) clearTimeout(tickHandle); };
  }, []);

  const sortedRows = useMemo(() => {
    if (!response?.results) return [];
    // Sort: items with forecast above floor first (best confidence on 7d
    // first), then below-floor items, then errors. Within each band by qty
    // × current_price desc so high-value items rise.
    const score = (r) => {
      if (r.error) return -1e6;
      const fc = (r.forecast?.horizons || []).find((h) => h.days === 7)?.confidence ?? 0;
      const value = (r.current_price || 0) * (r.qty || 1);
      return fc * 1000 + value;  // confidence dominates, value tiebreaks
    };
    return [...response.results].sort((a, b) => score(b) - score(a));
  }, [response]);

  if (phase === 'loading') {
    return <section className="forecast-all-page"><div className="loading">Loading collection…</div></section>;
  }

  if (phase === 'error') {
    return (
      <section className="forecast-all-page">
        <h2>Forecast All</h2>
        <div className="error">Forecast failed: {error}</div>
        <p><Link to="/">← Back to dashboard</Link></p>
      </section>
    );
  }

  if (phase === 'running') {
    return (
      <section className="forecast-all-page">
        <h2>Forecast All</h2>
        <div className="forecast-running">
          <div className="spinner" />
          <p>Forecasting {itemCount} item{itemCount === 1 ? '' : 's'}… {elapsedSec}s</p>
          <p className="muted">
            Cold-cache runs take 5-15s per item, fanned out 4-wide on the Pi.
            Cached re-runs finish in under a second.
          </p>
        </div>
      </section>
    );
  }

  if (phase === 'done' && (!response || response.results.length === 0)) {
    return (
      <section className="forecast-all-page">
        <h2>Forecast All</h2>
        <p>No items in your collection yet. Add some cards or sealed products first.</p>
        <p><Link to="/">← Back to dashboard</Link></p>
      </section>
    );
  }

  return (
    <section className="forecast-all-page">
      <h2>Forecast All</h2>

      <div className="forecast-disclaimer">
        Speculative projection from an LLM. NOT investment advice.
        Per-item confidence is the model's own self-rating, capped server-side
        by a rubric on history depth, volatility, and source agreement.
      </div>

      <div className="forecast-summary muted">
        {response.results.length} items · {response.duration_seconds.toFixed(1)}s ·{' '}
        {response.cache_hits} cache hit{response.cache_hits === 1 ? '' : 's'} ·{' '}
        {response.cache_misses} fresh forecast{response.cache_misses === 1 ? '' : 's'} ·{' '}
        model: {response.model}
      </div>

      <AggregateBanner aggregate={response.aggregate} />

      <h3 className="section-title">Per-item</h3>
      <table className="forecast-table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Qty</th>
            <th>Current</th>
            <th>7d</th>
            <th>30d</th>
            <th>90d</th>
            <th>Conf (7d)</th>
            <th>Direction</th>
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((r) => (
            <ForecastRow key={`${r.type}-${r.id}`} row={r} />
          ))}
        </tbody>
      </table>

      <p><Link to="/">← Back to dashboard</Link></p>
    </section>
  );
};

const AggregateBanner = ({ aggregate }) => {
  if (!aggregate || aggregate.length === 0) return null;
  return (
    <div className="forecast-aggregate">
      {aggregate.map((h) => {
        const delta = fmtDelta(h.current_total, h.projected_target);
        const deltaCls = delta && delta.startsWith('+') ? 'up'
                        : delta && delta.startsWith('-') ? 'down' : 'flat';
        return (
          <div key={h.days} className="aggregate-tile">
            <div className="aggregate-horizon">{horizonLabel(h.days)} outlook</div>
            <div className="aggregate-current">Now: {fmtMoney(h.current_total)}</div>
            <div className="aggregate-target">
              Target: <strong>{fmtMoney(h.projected_target)}</strong>
              {delta && <span className={`aggregate-delta ${deltaCls}`}> ({delta})</span>}
            </div>
            <div className="aggregate-range muted">
              Range: {fmtMoney(h.projected_low)} – {fmtMoney(h.projected_high)}
            </div>
            <div className="aggregate-weighted muted">
              Confidence-weighted: {fmtMoney(h.confidence_weighted_target)}
            </div>
            <div className="aggregate-counts muted">
              {h.items_included} included · {h.items_skipped} skipped (low confidence)
            </div>
          </div>
        );
      })}
    </div>
  );
};

const ForecastRow = ({ row }) => {
  const detailPath = row.type === 'card' ? `/cards/${row.id}` : `/sealed/${row.id}`;
  const h7 = row.forecast?.horizons?.find((h) => h.days === 7);
  const h30 = row.forecast?.horizons?.find((h) => h.days === 30);
  const h90 = row.forecast?.horizons?.find((h) => h.days === 90);
  const conf7 = h7?.confidence ?? 0;
  const direction = row.forecast?.direction || 'unknown';
  const caveats = row.forecast?.caveats || [];
  const tooltip = row.error
    ? `Error: ${row.error}`
    : caveats.length ? caveats.join(' · ') : '';

  return (
    <tr className={row.error ? 'row-error' : ''} title={tooltip}>
      <td>
        <Link to={detailPath}>{row.name}</Link>
        <span className="type-chip" data-type={row.type}>{row.type}</span>
      </td>
      <td>{row.qty}</td>
      <td>{fmtMoney(row.current_price)}</td>
      <td>{h7 ? fmtMoney(h7.target) : '—'}</td>
      <td>{h30 ? fmtMoney(h30.target) : '—'}</td>
      <td>{h90 ? fmtMoney(h90.target) : '—'}</td>
      <td>
        {h7 ? (
          <span className={`confidence-pill ${confidenceClass(conf7)}`}>
            {(conf7 * 100).toFixed(0)}%
          </span>
        ) : '—'}
      </td>
      <td className={`direction direction-${direction}`}>{directionArrow(direction)}</td>
    </tr>
  );
};

export default ForecastAllPage;
