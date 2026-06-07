import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';

/**
 * AlertPanel — the live price-alert feed.
 *
 * Mounted on the Dashboard. Pulls /alerts (triggered, non-muted watches) and
 * renders each as a neon card: which item moved, the direction, and the percent
 * change vs its baseline. "Dismiss" acknowledges the alert (mutes it until the
 * price recovers and re-crosses the threshold).
 */
const fmtMoney = (n) => (n == null ? '—' : `$${Number(n).toFixed(2)}`);

const AlertPanel = () => {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const res = await api.getAlerts();
      setAlerts(res.data);
    } catch {
      // Non-critical surface — leave the panel empty rather than break the page.
      setAlerts([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const dismiss = async (id) => {
    // Optimistic: drop it from the list, then ack server-side.
    setAlerts((prev) => prev.filter((a) => a.id !== id));
    try { await api.ackAlert(id); } catch { load(); }
  };

  if (loading) return null;
  if (alerts.length === 0) return null;  // no alerts → no clutter on the dashboard

  return (
    <div className="alert-panel">
      <div className="alert-panel-header">
        <h3>🔔 Price Alerts</h3>
        <span className="alert-count">{alerts.length}</span>
      </div>
      <ul className="alert-list">
        {alerts.map((a) => {
          const up = a.pct_change >= 0;
          const href = a.item_type === 'sealed'
            ? `/sealed/${a.item_id}` : `/cards/${a.item_id}`;
          return (
            <li key={a.id} className={`alert-item ${up ? 'alert-up' : 'alert-down'}`}>
              <Link to={href} className="alert-link">
                <span className="alert-move">{up ? '▲' : '▼'} {Math.abs(a.pct_change)}%</span>
                <span className="alert-prices">
                  {fmtMoney(a.baseline_price)} → {fmtMoney(a.current_price)}
                </span>
                {a.note && <span className="alert-note">{a.note}</span>}
              </Link>
              <button
                className="alert-dismiss"
                onClick={() => dismiss(a.id)}
                title="Dismiss until it re-crosses your threshold"
              >
                Dismiss
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
};

export default AlertPanel;
