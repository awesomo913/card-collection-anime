import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../services/api';
import { PRODUCT_TYPES_BY_GAME } from '../data/options';

const EMPTY = {
  name: '',
  set_name: '',
  product_type: 'Booster Box',
  quantity: 1,
  purchase_price: '',
  game: 'magic',
  notes: '',
};

const AddSealedPage = () => {
  const navigate = useNavigate();
  const { id } = useParams();
  const [sealed, setSealed] = useState(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    api.getSealedProduct(id)
      .then((res) => setSealed({ ...EMPTY, ...res.data }))
      .catch((err) => { console.error(err); setError('Failed to load sealed product'); })
      .finally(() => setLoading(false));
  }, [id]);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setSealed((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSaved(false);
    try {
      const payload = {
        ...sealed,
        quantity: parseInt(sealed.quantity, 10) || 1,
        purchase_price: sealed.purchase_price === '' || sealed.purchase_price == null
          ? null
          : parseFloat(sealed.purchase_price),
      };
      if (id) await api.updateSealedProduct(id, payload);
      else await api.createSealedProduct(payload);
      setSaved(true);
      setTimeout(() => navigate('/sealed'), 800);
    } catch (err) {
      console.error(err);
      setError(err?.response?.data?.detail || 'Failed to save sealed product');
    } finally {
      setLoading(false);
    }
  };

  if (loading && !saved) return <div className="loading">Working…</div>;
  if (saved) return <div className="loading">Saved! Redirecting…</div>;

  return (
    <section>
      <h2>{id ? 'Edit Sealed Product' : 'Add Sealed Product'}</h2>
      {error && <div className="error">{error}</div>}

      <form onSubmit={handleSubmit}>
        <div>
          <label>Game</label>
          <select name="game" value={sealed.game} onChange={handleChange}>
            <option value="magic">Magic: The Gathering</option>
            <option value="pokemon">Pokémon</option>
            <option value="yugioh">Yu-Gi-Oh!</option>
          </select>
        </div>
        <div>
          <label>Name</label>
          <input type="text" name="name" value={sealed.name} onChange={handleChange} required />
        </div>
        <div>
          <label>Set</label>
          <input type="text" name="set_name" value={sealed.set_name} onChange={handleChange} required />
        </div>
        <div>
          <label>Product Type (pick or type)</label>
          <input
            type="text"
            name="product_type"
            value={sealed.product_type}
            onChange={handleChange}
            list="sealed-product-type-options"
            autoComplete="off"
            required
          />
          <datalist id="sealed-product-type-options">
            {(PRODUCT_TYPES_BY_GAME[sealed.game] || []).map((t) => (
              <option key={t} value={t} />
            ))}
          </datalist>
        </div>
        <div>
          <label>Quantity</label>
          <input type="number" name="quantity" value={sealed.quantity} onChange={handleChange} min="1" />
        </div>
        <div>
          <label>Purchase Price</label>
          <input type="number" name="purchase_price" value={sealed.purchase_price ?? ''} onChange={handleChange} step="0.01" />
        </div>
        <div>
          <label>Notes</label>
          <textarea name="notes" value={sealed.notes || ''} onChange={handleChange} />
        </div>

        <button type="submit" disabled={loading}>{loading ? 'Saving…' : 'Save Sealed Product'}</button>
        <button type="button" onClick={() => navigate('/sealed')}>Cancel</button>
      </form>
    </section>
  );
};

export default AddSealedPage;
