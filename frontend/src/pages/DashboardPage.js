import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';

const DashboardPage = () => {
  const [stats, setStats] = useState({ total: 0, cards: 0, sealed: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let mounted = true;
    Promise.all([api.getCollectionValue(), api.getCards(), api.getSealedProducts()])
      .then(([valueRes, cardsRes, sealedRes]) => {
        if (!mounted) return;
        setStats({
          total: valueRes.data.total_value || 0,
          cards: cardsRes.data.length || 0,
          sealed: sealedRes.data.length || 0,
        });
      })
      .catch(() => mounted && setError('Failed to load dashboard'))
      .finally(() => mounted && setLoading(false));
    return () => { mounted = false; };
  }, []);

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">Error: {error}</div>;

  return (
    <section>
      <h2>Dashboard</h2>
      <div className="dashboard-stats">
        <div className="stat-card">
          <h3>Total Value</h3>
          <p className="stat-value">${stats.total.toFixed(2)}</p>
        </div>
        <div className="stat-card">
          <h3>Single Cards</h3>
          <p className="stat-value">{stats.cards}</p>
        </div>
        <div className="stat-card">
          <h3>Sealed Products</h3>
          <p className="stat-value">{stats.sealed}</p>
        </div>
      </div>

      <div className="quick-actions">
        <h3>Quick Actions</h3>
        <Link to="/cards/add" className="add-button">+ Add Card</Link>
        <Link to="/sealed/add" className="add-button" style={{ marginLeft: 10 }}>+ Add Sealed</Link>
        <Link to="/snapshot" className="add-button" style={{ marginLeft: 10 }}>View Price Snapshot</Link>
      </div>
    </section>
  );
};

export default DashboardPage;
