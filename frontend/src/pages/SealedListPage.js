import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';
import TileCard from '../components/TileCard';

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
      </div>

      {filtered.length === 0 ? (
        <p className="empty-state">
          No sealed products yet. <Link to="/sealed/add">Add your first one</Link>.
        </p>
      ) : (
        <div className="sealed-grid">
          {filtered.map((s) => (
            <TileCard key={s.id} item={s} onDelete={handleDelete} />
          ))}
        </div>
      )}
    </section>
  );
};

export default SealedListPage;
