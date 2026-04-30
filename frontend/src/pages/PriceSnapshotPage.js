import React, { useEffect, useState } from 'react';
import api from '../services/api';

const PriceSnapshotPage = () => {
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = () => {
    setLoading(true);
    api.getSnapshot()
      .then((res) => setSnapshot(res.data))
      .catch(() => setError('Failed to load snapshot'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const triggerRefresh = async () => {
    setRefreshing(true);
    try {
      await api.triggerPriceUpdate();
      // Brief delay for backend to commit before re-fetching.
      await new Promise((r) => setTimeout(r, 600));
      load();
    } catch {
      setError('Refresh failed');
    } finally {
      setRefreshing(false);
    }
  };

  if (loading) return <div className="loading">Loading snapshot…</div>;
  if (error) return <div className="error">Error: {error}</div>;

  const bySource = snapshot?.by_source || {};
  const entries = Object.entries(bySource);
  const maxValue = entries.length ? Math.max(...entries.map(([, v]) => v)) : 1;

  return (
    <section className="anime-snapshot">
      <h2>Price Snapshot</h2>
      <p>As of: {snapshot?.timestamp || 'unknown'}</p>

      {entries.length === 0 ? (
        <p className="empty-state">No price data yet — add a card or trigger a refresh.</p>
      ) : (
        <div className="snapshot-bars">
          {entries.map(([source, value]) => {
            const widthPct = Math.max(2, Math.min(100, (value / maxValue) * 100));
            return (
              <div className="source-bar" key={source} data-source={source}>
                <span className="bar-label">{source}</span>
                <div className="bar" style={{ width: `${widthPct}%` }} aria-hidden="true" />
                <span className="bar-value">${value.toFixed(2)}</span>
              </div>
            );
          })}
        </div>
      )}

      <div className="snapshot-total">
        <span className="label">Total Collection Value</span>
        <span className="value">${(snapshot?.total_value ?? 0).toFixed(2)}</span>
      </div>

      <div style={{ textAlign: 'center', marginTop: 18 }}>
        <button onClick={triggerRefresh} disabled={refreshing}>
          {refreshing ? 'Refreshing…' : 'Refresh Prices Now'}
        </button>
      </div>
    </section>
  );
};

export default PriceSnapshotPage;
