import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../services/api';

const AddCardPage = () => {
  const navigate = useNavigate();
  const { id } = useParams();
  const [card, setCard] = useState({
    name: '',
    set_name: '',
    card_number: '',
    rarity: '',
    condition: '',
    quantity: 1,
    purchase_price: '',
    is_foil: false,
    is_signed: false,
    game: 'magic',
    notes: ''
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    if (id) {
      const fetchCard = async () => {
        try {
          setLoading(true);
          const response = await api.getCard(id);
          setCard(response.data);
        } catch (err) {
          setError('Failed to fetch card');
          console.error(err);
        } finally {
          setLoading(false);
        }
      };

      fetchCard();
    }
  }, [id]);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setCard(prev => ({
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
      const cardData = {
        ...card,
        purchase_price: card.purchase_price === '' ? null : parseFloat(card.purchase_price),
        quantity: parseInt(card.quantity) || 1
      };

      if (id) {
        await api.updateCard(id, cardData);
      } else {
        await api.createCard(cardData);
      }
      
      setSuccess(true);
      setTimeout(() => {
        navigate('/cards');
      }, 1500);
    } catch (err) {
      setError('Failed to save card');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div>Saving...</div>;
  if (error) return <div>Error: {error}</div>;
  if (success) return <div>Card saved successfully!</div>;

  return (
    <div>
      <h2>{id ? 'Edit Card' : 'Add New Card'}</h2>
      <form onSubmit={handleSubmit}>
        <div>
          <label>Name:</label>
          <input 
            type="text" 
            name="name" 
            value={card.name} 
            onChange={handleChange} 
            required
          />
        </div>
        <div>
          <label>Set:</label>
          <input 
            type="text" 
            name="set_name" 
            value={card.set_name} 
            onChange={handleChange} 
            required
          />
        </div>
        <div>
          <label>Card Number (optional):</label>
          <input 
            type="text" 
            name="card_number" 
            value={card.card_number} 
            onChange={handleChange}
          />
        </div>
        <div>
          <label>Rarity (optional):</label>
          <input 
            type="text" 
            name="rarity" 
            value={card.rarity} 
            onChange={handleChange}
          />
        </div>
        <div>
          <label>Condition (optional):</label>
          <input 
            type="text" 
            name="condition" 
            value={card.condition} 
            onChange={handleChange}
          />
        </div>
        <div>
          <label>Quantity:</label>
          <input 
            type="number" 
            name="quantity" 
            value={card.quantity} 
            onChange={handleChange} 
            min="1"
          />
        </div>
        <div>
          <label>Purchase Price (optional):</label>
          <input 
            type="number" 
            name="purchase_price" 
            value={card.purchase_price || ''} 
            onChange={handleChange} 
            step="0.01"
          />
        </div>
        <div>
          <label>Game:</label>
          <select 
            name="game" 
            value={card.game} 
            onChange={handleChange}
          >
            <option value="magic">Magic: The Gathering</option>
            <option value="pokemon">Pokémon</option>
            <option value="yugioh">Yu-Gi-Oh!</option>
          </select>
        </div>
        <div>
          <label>
            <input 
              type="checkbox" 
              name="is_foil" 
              checked={card.is_foil} 
              onChange={handleChange} 
            />
            Foil
          </label>
        </div>
        <div>
          <label>
            <input 
              type="checkbox" 
              name="is_signed" 
              checked={card.is_signed} 
              onChange={handleChange} 
            />
            Signed
          </label>
        </div>
        <div>
          <label>Notes (optional):</label>
          <textarea 
            name="notes" 
            value={card.notes} 
            onChange={handleChange}
          />
        </div>
        <button type="submit" disabled={loading}>
          {loading ? 'Saving...' : 'Save Card'}
        </button>
        <button type="button" onClick={() => navigate('/cards')}>Cancel</button>
      </form>
    </div>
  );
};

export default AddCardPage;