import React, { useState } from 'react';
import api from '../services/api';

/**
 * ForecastPanel — DeepSeek-powered short-term price projection.
 *
 * Mounted on both CardDetailPage and SealedDetailPage. On-demand only:
 * shows a button "Get forecast" — first click hits the backend which calls
 * DeepSeek. Server caches the result for 24h (or until a new PriceHistory
 * row lands for this item, whichever comes first).
 *
 * SPECULATIVE — explicit disclaimer rendered at the bottom. LLM projections
 * are not investment advice; the UI says so.
 *
 * Props:
 *   itemType   'card' | 'sealed'
 *   itemId     numeric ID of the card/sealed product
 */

const directionEmoji = {
  up: '📈',
  down: '📉',
  flat: '➖',
  unknown: '❔',
};
const directionLabel = {
  up: 'Trending up',
  down: 'Trending down',
  flat: 'Flat',
  unknown: 'Direction unclear',
};

const fmtMoney = (n) => (n == null ? '—' : `$${Number(n).toFixed(2)}`);
const fmtTs = (iso) => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
};

const ForecastPanel = ({ itemType, itemId }) => {
  const [forecast, setForecast] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchForecast = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = itemType === 'sealed'
        ? await api.forecastSealed(itemId)
        : await api.forecastCard(itemId);
      setForecast(res.data);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      if (err?.response?.status === 503) {
        setError(
          detail || 'Forecast unavailable: DEEPSEEK_API_KEY not set on the Pi.'
        );
      } else {
        setError(detail || err.message || 'Forecast failed.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="forecast-panel">
      <div className="forecast-header">
        <h3>Price forecast</h3>
        <button
          type="button"
          onClick={fetchForecast}
          disabled={loading}
          className="primary"
        >
          {loading ? 'Forecasting…' : forecast ? 'Refresh forecast' : 'Get forecast'}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {!forecast && !loading && !error && (
        <p className="muted">
          Click <strong>Get forecast</strong> to ask DeepSeek where this item's
          price might go over the next 7 / 30 / 90 days based on its history.
        </p>
      )}

      {forecast && (
        <>
          <div className="forecast-headline" data-direction={forecast.direction}>
            <span className="dir-emoji">{directionEmoji[forecast.direction] || '❔'}</span>
            <span className="dir-label">{directionLabel[forecast.direction] || 'Unknown'}</span>
            {forecast.current_price != null && (
              <span className="dir-now muted">
                · now {fmtMoney(forecast.current_price)}
              </span>
            )}
          </div>

          {forecast.horizons.length === 0 && (
            <div className="muted">
              No horizons returned. {forecast.caveats?.[0] || ''}
            </div>
          )}

          {forecast.horizons.length > 0 && (
            <table className="forecast-table">
              <thead>
                <tr>
                  <th>Horizon</th>
                  <th>Low</th>
                  <th>Target</th>
                  <th>High</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {forecast.horizons.map((h) => {
                  const dir = forecast.current_price != null
                    ? (h.target > forecast.current_price ? 'up' : (h.target < forecast.current_price ? 'down' : 'flat'))
                    : null;
                  return (
                    <tr key={h.days} data-dir={dir}>
                      <td>{h.days}-day</td>
                      <td>{fmtMoney(h.low)}</td>
                      <td className="target">{fmtMoney(h.target)}</td>
                      <td>{fmtMoney(h.high)}</td>
                      <td>{(h.confidence * 100).toFixed(0)}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {forecast.reasoning && (
            <p className="forecast-reasoning">{forecast.reasoning}</p>
          )}

          {forecast.drivers?.length > 0 && (
            <div className="forecast-bullets">
              <strong>Drivers:</strong>
              <ul>
                {forecast.drivers.map((d, i) => <li key={i}>{d}</li>)}
              </ul>
            </div>
          )}

          {forecast.caveats?.length > 0 && (
            <div className="forecast-bullets caveats">
              <strong>Risks:</strong>
              <ul>
                {forecast.caveats.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </div>
          )}

          <div className="forecast-footer muted">
            Generated {fmtTs(forecast.generated_at)} ·{' '}
            {forecast.cached ? 'cached' : 'fresh'} ·{' '}
            model: {forecast.model} ·{' '}
            {forecast.history_samples_used} history samples used
          </div>
          <div className="forecast-disclaimer">
            ⚠ Speculative projection from an LLM analysing past prices.
            NOT investment or financial advice. TCG markets are volatile and
            can move beyond any projected range. Use as one signal among many.
          </div>
        </>
      )}
    </div>
  );
};

export default ForecastPanel;
