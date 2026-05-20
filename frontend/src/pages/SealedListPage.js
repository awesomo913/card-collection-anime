import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';
import TileCard from '../components/TileCard';
import { groupByGame } from '../utils/groupByGame';

const SealedListPage = () => {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    api.getSealedProducts()
      .then((res) => { if (mounted) setItems(res.data); })
      .catch((err) => { if (mounted) setError('Failed to fetch sealed products'); console.error(err); })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, []);

  const filtered = items.filter((s) =>
    s.name.toLowerCase().includes(search.toLowerCase()) ||
    (s.set_name || '').toLowerCase().includes(search.toLowerCase())
  );

  // Header summary — total units + value across what's currently shown.
  // Mirrors CardListPage's .filter-summary. Quantity defaults to 1 when the
  // field is missing/non-finite so a product without an explicit qty still counts.
  const totalUnits = filtered.reduce(
    (sum, s) => sum + (Number.isFinite(s.quantity) ? s.quantity : 1), 0
  );
  const totalValue = filtered.reduce(
    (sum, s) => sum + ((s.current_price || 0) * (Number.isFinite(s.quantity) ? s.quantity : 1)), 0
  );

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this sealed product?')) return;
    try {
      await api.deleteSealedProduct(id);
      setItems((prev) => prev.filter((s) => s.id !== id));
    } catch (err) {
      setError('Failed to delete sealed product');
    }
  };

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">Error: {error}</div>;

  return (
    <section>
      <h2>Sealed Products</h2>
      <div className="search-bar">
        <input
          type="text"
          placeholder="Search by name or set…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Link to="/sealed/add" className="add-button">+ Add Sealed</Link>
        <Link to="/forecast-all?scope=sealed" className="add-button">📈 Forecast Sealed</Link>
      </div>

      <div className="filter-summary" aria-live="polite">
        <strong>{filtered.length}</strong> product{filtered.length === 1 ? '' : 's'}
        {totalUnits !== filtered.length && (
          <> &nbsp;·&nbsp; <strong>{totalUnits}</strong> total units</>
        )}
        {totalValue > 0 && (
          <> &nbsp;·&nbsp; <strong>${totalValue.toFixed(2)}</strong> value</>
        )}
      </div>

      {filtered.length === 0 ? (
        <p className="empty-state">
          No sealed products yet. <Link to="/sealed/add">Add your first one</Link>.
        </p>
      ) : (
        groupByGame(filtered).map((section) => (
          <div key={section.key} className="game-section">
            <h3 className="game-section-header" data-game={section.key}>
              {section.label}{' '}
              <span className="game-section-count">({section.items.length})</span>
            </h3>
            <div className="sealed-grid">
              {section.items.map((s) => (
                <TileCard key={s.id} item={s} onDelete={handleDelete} />
              ))}
            </div>
          </div>
        ))
      )}
    </section>
  );
};

export default SealedListPage;
