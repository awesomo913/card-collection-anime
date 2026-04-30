import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';
import TileCard from '../components/TileCard';

const CardListPage = () => {
  const [cards, setCards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    api.getCards()
      .then((res) => { if (mounted) setCards(res.data); })
      .catch((err) => { if (mounted) setError('Failed to fetch cards'); console.error(err); })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, []);

  const filtered = cards.filter((c) =>
    c.name.toLowerCase().includes(search.toLowerCase()) ||
    (c.set_name || '').toLowerCase().includes(search.toLowerCase())
  );

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this card?')) return;
    try {
      await api.deleteCard(id);
      setCards((prev) => prev.filter((c) => c.id !== id));
    } catch (err) {
      setError('Failed to delete card');
    }
  };

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">Error: {error}</div>;

  return (
    <section>
      <h2>My Cards</h2>
      <div className="search-bar">
        <input
          type="text"
          placeholder="Search by name or set…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Link to="/cards/add" className="add-button">+ Add Card</Link>
      </div>

      {filtered.length === 0 ? (
        <p className="empty-state">
          No cards yet. <Link to="/cards/add">Add your first card</Link>.
        </p>
      ) : (
        <div className="card-grid">
          {filtered.map((c) => (
            <TileCard key={c.id} item={c} onDelete={handleDelete} />
          ))}
        </div>
      )}
    </section>
  );
};

export default CardListPage;
