import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../services/api';
import CatalogSearch from '../components/CatalogSearch';
import { CONDITIONS, RARITIES_BY_GAME } from '../data/options';

const EMPTY_CARD = {
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
  notes: '',
  external_source: null,
  external_id: null,
  image_url: null,
  tcgplayer_product_id: null,
};

const AddCardPage = () => {
  const navigate = useNavigate();
  const { id } = useParams();
  const [card, setCard] = useState(EMPTY_CARD);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    api.getCard(id)
      .then((res) => setCard({ ...EMPTY_CARD, ...res.data }))
      .catch((err) => { console.error(err); setError('Failed to load card'); })
      .finally(() => setLoading(false));
  }, [id]);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setCard((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }));
  };

  // When the user picks a result from the live TCG search, slam the relevant
  // catalog fields into the form and pin external_source/external_id so the
  // backend will refresh prices via that exact catalog ID forever after.
  const handlePick = (result) => {
    const sourceToGame = {
      scryfall: 'magic',
      pokemontcg: 'pokemon',
      ygoprodeck: 'yugioh',
    };
    setCard((prev) => ({
      ...prev,
      name: result.name || prev.name,
      set_name: result.set_name || prev.set_name,
      rarity: result.rarity || prev.rarity,
      // Sync the game dropdown to whichever catalog the result came from.
      // (For 'tcgplayer' OG-scrape results we can't tell, so we leave game alone.)
      game: sourceToGame[result.external_source] || prev.game,
      external_source: result.external_source,
      external_id: result.external_id,
      image_url: result.image_url || null,
      // Pin TCGplayer product ID when present so future refreshes hit
      // TCGplayer's marketPrice (per-printing) instead of the catalog source's
      // aggregate or zero-data per-printing entry (e.g. YGOPRODeck Starlight Rare).
      tcgplayer_product_id: result.tcgplayer_product_id || prev.tcgplayer_product_id || null,
    }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSaved(false);
    try {
      const payload = {
        ...card,
        quantity: parseInt(card.quantity, 10) || 1,
        purchase_price: card.purchase_price === '' || card.purchase_price == null
          ? null
          : parseFloat(card.purchase_price),
      };
      if (id) await api.updateCard(id, payload);
      else await api.createCard(payload);
      setSaved(true);
      setTimeout(() => navigate('/cards'), 800);
    } catch (err) {
      console.error(err);
      setError(err?.response?.data?.detail || 'Failed to save card');
    } finally {
      setLoading(false);
    }
  };

  if (loading && !saved) return <div className="loading">Working…</div>;
  if (saved) return <div className="loading">Saved! Redirecting…</div>;

  return (
    <section>
      <h2>{id ? 'Edit Card' : 'Add a Card'}</h2>
      {error && <div className="error">{error}</div>}

      {!id && (
        <div className="catalog-search-host">
          <div className="catalog-game-row">
            <label>
              Game
              <select name="game" value={card.game} onChange={handleChange}>
                <option value="magic">Magic: The Gathering</option>
                <option value="pokemon">Pokémon</option>
                <option value="yugioh">Yu-Gi-Oh!</option>
              </select>
            </label>
            {card.external_source && card.external_id && (
              <span className="linked-badge" title={`${card.external_source}:${card.external_id}`}>
                ✓ Linked to {card.external_source}
              </span>
            )}
          </div>
          <CatalogSearch game={card.game} onPick={handlePick} />
          <p className="catalog-hint">
            Search picks the exact product so future price refreshes stay accurate.
            You can also fill the form manually below.
          </p>
        </div>
      )}

      <form onSubmit={handleSubmit}>
        {card.image_url && (
          <div className="form-image-preview">
            <img src={card.image_url} alt={card.name || 'Card preview'} />
          </div>
        )}

        <div>
          <label>Name</label>
          <input type="text" name="name" value={card.name} onChange={handleChange} required />
        </div>
        <div>
          <label>Set</label>
          <input type="text" name="set_name" value={card.set_name} onChange={handleChange} required />
        </div>
        <div>
          <label>Card Number</label>
          <input type="text" name="card_number" value={card.card_number || ''} onChange={handleChange} />
        </div>
        <div>
          <label>Rarity</label>
          <input
            type="text"
            name="rarity"
            value={card.rarity || ''}
            onChange={handleChange}
            list="rarity-options"
            autoComplete="off"
          />
          <datalist id="rarity-options">
            {(RARITIES_BY_GAME[card.game] || []).map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
        </div>
        <div>
          <label>Condition</label>
          <input
            type="text"
            name="condition"
            value={card.condition || ''}
            onChange={handleChange}
            list="condition-options"
            autoComplete="off"
          />
          <datalist id="condition-options">
            {CONDITIONS.map((c) => <option key={c} value={c} />)}
          </datalist>
        </div>
        <div>
          <label>Quantity</label>
          <input type="number" name="quantity" value={card.quantity} onChange={handleChange} min="1" />
        </div>
        <div>
          <label>Purchase Price</label>
          <input type="number" name="purchase_price" value={card.purchase_price ?? ''} onChange={handleChange} step="0.01" />
        </div>
        {id && (
          <div>
            <label>Game</label>
            <select name="game" value={card.game} onChange={handleChange}>
              <option value="magic">Magic: The Gathering</option>
              <option value="pokemon">Pokémon</option>
              <option value="yugioh">Yu-Gi-Oh!</option>
            </select>
          </div>
        )}
        <div className="checkbox-row">
          <label>
            <input type="checkbox" name="is_foil" checked={!!card.is_foil} onChange={handleChange} />
            Foil
          </label>
          <label>
            <input type="checkbox" name="is_signed" checked={!!card.is_signed} onChange={handleChange} />
            Signed
          </label>
        </div>
        <div>
          <label>Notes</label>
          <textarea name="notes" value={card.notes || ''} onChange={handleChange} />
        </div>

        <button type="submit" disabled={loading}>{loading ? 'Saving…' : 'Save Card'}</button>
        <button type="button" onClick={() => navigate('/cards')}>Cancel</button>
      </form>
    </section>
  );
};

export default AddCardPage;
