import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../services/api';

const AddSealedPage = () => {
  const navigate = useNavigate();
  const { id } = useParams();
  const [sealed, setSealed] = useState({
    name: '',
    set_name: '',
    product_type: 'booster box',
    quantity: 1,
    purchase_price: '',
    game: 'magic',
    notes: ''
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    if (id) {
      const fetchSealedProduct = async () => {
        try {
          setLoading(true);
          const response = await api.getSealedProduct(id);
          setSealed(response.data);
        } catch (err) {
          setError('Failed to fetch sealed product');
          console.error(err);
        } finally {
          setLoading(false);
        }
      };

      fetchSealedProduct();
    }
  }, [id]);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setSealed(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value
    }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      setLoading(true);
      setError(null);
      setSuccess(false);
      
      // Convert empty strings to null for numeric fields
      const sealedData = {
        ...sealed,
        purchase_price: sealed.purchase_price === '' ? null : parseFloat(sealed.purchase_price),
        quantity: parseInt(sealed.quantity) || 1
      };

      if (id) {
        await api.updateSealedProduct(id, sealedData);
      } else {
        await api.createSealedProduct(sealedData);
      }
      
      setSuccess(true);
      setTimeout(() => {
        navigate('/sealed');
      }, 1500);
    } catch (err) {
      setError('Failed to save sealed product');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div>Saving...</div>;
  if (error) return <div>Error: {error}</div>;
  if (success) return <div>Sealed product saved successfully!</div>;

  return (
    <div>
      <h2>{id ? 'Edit Sealed Product' : 'Add New Sealed Product'}</h2>
      <form onSubmit={handleSubmit}>
        <div>
          <label>Name:</label>
          <input 
            type="text" 
            name="name" 
            value={sealed.name} 
            onChange={handleChange} 
            required
          />
        </div>
        <div>
          <label>Set:</label>
          <input 
            type="text" 
            name="set_name" 
            value={sealed.set_name} 
            onChange={handleChange} 
            required
          />
        </div>
        <div>
          <label>Product Type:</label>
          <select 
            name="product_type" 
            value={sealed.product_type} 
            onChange={handleChange}
          >
            <option value="booster box">Booster Box</option>
            <option value="pack">Pack</option>
            <option value="deck">Deck</option>
            <option value="box">Box</option>
            <option value="case">Case</option>
          </select>
        </div>
        <div>
          <label>Quantity:</label>
          <input 
            type="number" 
            name="quantity" 
            value={sealed.quantity} 
            onChange={handleChange} 
            min="1"
          />
        </div>
        <div>
          <label>Purchase Price (optional):</label>
          <input 
            type="number" 
            name="purchase_price" 
            value={sealed.purchase_price || ''} 
            onChange={handleChange} 
            step="0.01"
          />
        </div>
        <div>
          <label>Game:</label>
          <select 
            name="game" 
            value={sealed.game} 
            onChange={handleChange}
          >
            <option value="magic">Magic: The Gathering</option>
            <option value="pokemon">Pokémon</option>
            <option value="yugioh">Yu-Gi-Oh!</option>
          </select>
        </div>
        <div>
          <label>Notes (optional):</label>
          <textarea 
            name="notes" 
            value={sealed.notes} 
            onChange={handleChange}
          />
        </div>
        <button type="submit" disabled={loading}>
          {loading ? 'Saving...' : 'Save Sealed Product'}
        </button>
        <button type="button" onClick={() => navigate('/sealed')}>Cancel</button>
      </form>
    </div>
  );
};

export default AddSealedPage;