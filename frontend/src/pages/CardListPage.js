import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../services/api';
import TileCard from '../components/TileCard';

/* --------------------------------------------------------------------------
 * CardListPage — view + sort + filter the user's card collection.
 *
 * Phase D overhaul: replaces the previous substring-only filter with a full
 * filter panel (game chips, foil/signed toggles, rarity datalist, price
 * range slider) plus a sort dropdown. Filter state persists to localStorage
 * so it survives reloads and Pi restarts. Goal: find any card in <5 seconds.
 * ------------------------------------------------------------------------ */

const FILTER_STORAGE_KEY = 'card-list-filters-v1';

const DEFAULT_FILTERS = {
  search: '',
  games: ['magic', 'pokemon', 'yugioh'], // all selected = no filter
  foilOnly: false,
  signedOnly: false,
  rarity: '',
  minPrice: '',
  maxPrice: '',
  sort: 'newest', // newest | value-desc | value-asc | name-asc | recently-priced
};

const SORT_LABELS = {
  newest: 'Newest first',
  'value-desc': 'Highest value',
  'value-asc': 'Lowest value',
  'name-asc': 'A → Z',
  'recently-priced': 'Recently priced',
};

const GAME_LABEL = {
  magic: 'Magic',
  pokemon: 'Pokémon',
  yugioh: 'Yu-Gi-Oh!',
};

const sortComparator = (mode) => {
  switch (mode) {
    case 'value-desc':
      return (a, b) => (b.current_price ?? -1) - (a.current_price ?? -1);
    case 'value-asc':
      return (a, b) => (a.current_price ?? Infinity) - (b.current_price ?? Infinity);
    case 'name-asc':
      return (a, b) => (a.name || '').localeCompare(b.name || '');
    case 'recently-priced':
      return (a, b) => new Date(b.last_price_update || 0) - new Date(a.last_price_update || 0);
    case 'newest':
    default:
      return (a, b) => (b.id || 0) - (a.id || 0);
  }
};

const loadFilters = () => {
  try {
    const raw = localStorage.getItem(FILTER_STORAGE_KEY);
    if (!raw) return DEFAULT_FILTERS;
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_FILTERS, ...parsed };
  } catch {
    return DEFAULT_FILTERS;
  }
};

const CardListPage = () => {
  const [cards, setCards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filters, setFilters] = useState(loadFilters);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    api.getCards()
      .then((res) => { if (mounted) setCards(res.data); })
      .catch((err) => { if (mounted) setError('Failed to fetch cards'); console.error(err); })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, []);

  // Persist filter state. Throttled inline via a small ms tick to avoid
  // hammering localStorage on every keystroke of the search box.
  useEffect(() => {
    const handle = setTimeout(() => {
      try { localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(filters)); }
      catch (e) { console.warn('Filter persist failed:', e); }
    }, 200);
    return () => clearTimeout(handle);
  }, [filters]);

  // Build a list of unique rarities present in the collection — feeds the rarity
  // datalist. Only rarities that actually exist in the user's collection appear,
  // so we don't show useless options.
  const presentRarities = useMemo(() => {
    const set = new Set(cards.map((c) => (c.rarity || '').trim()).filter(Boolean));
    return Array.from(set).sort();
  }, [cards]);

  // Compute price range bounds for the slider — matches what's actually here.
  const priceBounds = useMemo(() => {
    const prices = cards.map((c) => c.current_price).filter((p) => p != null);
    if (prices.length === 0) return { min: 0, max: 1000 };
    return {
      min: Math.floor(Math.min(...prices)),
      max: Math.ceil(Math.max(...prices)),
    };
  }, [cards]);

  const filtered = useMemo(() => {
    const search = filters.search.toLowerCase().trim();
    const minP = filters.minPrice === '' ? -Infinity : parseFloat(filters.minPrice);
    const maxP = filters.maxPrice === '' ? Infinity : parseFloat(filters.maxPrice);
    const rarity = filters.rarity.trim().toLowerCase();
    return cards.filter((c) => {
      if (search && !(
        (c.name || '').toLowerCase().includes(search) ||
        (c.set_name || '').toLowerCase().includes(search) ||
        (c.notes || '').toLowerCase().includes(search)
      )) return false;
      // Game chips: empty array = nothing matches; fully selected = no constraint
      if (filters.games.length > 0 && filters.games.length < 3 &&
          !filters.games.includes((c.game || '').toLowerCase())) return false;
      if (filters.foilOnly && !c.is_foil) return false;
      if (filters.signedOnly && !c.is_signed) return false;
      if (rarity && (c.rarity || '').toLowerCase() !== rarity) return false;
      const p = c.current_price;
      if (p != null && (p < minP || p > maxP)) return false;
      // Cards with no price don't get filtered out by price range — let user see them.
      return true;
    }).sort(sortComparator(filters.sort));
  }, [cards, filters]);

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this card?')) return;
    try {
      await api.deleteCard(id);
      setCards((prev) => prev.filter((c) => c.id !== id));
    } catch (err) {
      setError('Failed to delete card');
    }
  };

  const toggleGame = (g) => setFilters((f) => ({
    ...f,
    games: f.games.includes(g) ? f.games.filter((x) => x !== g) : [...f.games, g],
  }));

  const clearFilters = () => setFilters(DEFAULT_FILTERS);

  const filtersActive = (
    filters.search ||
    filters.games.length < 3 ||
    filters.foilOnly ||
    filters.signedOnly ||
    filters.rarity ||
    filters.minPrice !== '' ||
    filters.maxPrice !== '' ||
    filters.sort !== 'newest'
  );

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">Error: {error}</div>;

  return (
    <section className="card-list-page">
      <h2>My Cards</h2>

      <div className="filter-panel" role="region" aria-label="Card filters">
        <div className="filter-row">
          <input
            type="text"
            className="filter-search"
            placeholder="Search by name, set, or notes…"
            value={filters.search}
            onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
          />
          <select
            className="filter-sort"
            value={filters.sort}
            onChange={(e) => setFilters((f) => ({ ...f, sort: e.target.value }))}
            aria-label="Sort"
          >
            {Object.entries(SORT_LABELS).map(([val, label]) => (
              <option key={val} value={val}>{label}</option>
            ))}
          </select>
          <Link to="/cards/add" className="add-button">+ Add Card</Link>
        </div>

        <div className="filter-row chips-row">
          {Object.entries(GAME_LABEL).map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`game-chip${filters.games.includes(key) ? ' active' : ''}`}
              data-game={key}
              onClick={() => toggleGame(key)}
              aria-pressed={filters.games.includes(key)}
            >
              {label}
            </button>
          ))}
          <label className="toggle">
            <input
              type="checkbox"
              checked={filters.foilOnly}
              onChange={(e) => setFilters((f) => ({ ...f, foilOnly: e.target.checked }))}
            />
            Foil only
          </label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={filters.signedOnly}
              onChange={(e) => setFilters((f) => ({ ...f, signedOnly: e.target.checked }))}
            />
            Signed only
          </label>
        </div>

        <div className="filter-row">
          <label className="filter-field">
            Rarity
            <input
              type="text"
              list="rarity-filter-options"
              value={filters.rarity}
              onChange={(e) => setFilters((f) => ({ ...f, rarity: e.target.value }))}
              placeholder="Any"
            />
            <datalist id="rarity-filter-options">
              {presentRarities.map((r) => <option key={r} value={r} />)}
            </datalist>
          </label>
          <label className="filter-field">
            Min $
            <input
              type="number"
              step="0.01"
              min={priceBounds.min}
              max={priceBounds.max}
              value={filters.minPrice}
              onChange={(e) => setFilters((f) => ({ ...f, minPrice: e.target.value }))}
              placeholder={`$${priceBounds.min}`}
            />
          </label>
          <label className="filter-field">
            Max $
            <input
              type="number"
              step="0.01"
              min={priceBounds.min}
              max={priceBounds.max}
              value={filters.maxPrice}
              onChange={(e) => setFilters((f) => ({ ...f, maxPrice: e.target.value }))}
              placeholder={`$${priceBounds.max}`}
            />
          </label>
          {filtersActive && (
            <button type="button" className="ghost clear-filters" onClick={clearFilters}>
              Clear filters
            </button>
          )}
        </div>

        <div className="filter-summary" aria-live="polite">
          {(() => {
            // Per-set summary: rows shown vs total, plus a quantity count and
            // a price×qty total for whatever's currently visible. Helps the
            // user see "Yu-Gi-Oh foils above $5" sum to a specific dollar
            // amount without leaving the page.
            const totalQty = filtered.reduce(
              (sum, c) => sum + (Number.isFinite(c.quantity) ? c.quantity : 1), 0
            );
            const totalValue = filtered.reduce(
              (sum, c) => sum + ((c.current_price || 0) * (Number.isFinite(c.quantity) ? c.quantity : 1)), 0
            );
            const collectionQty = cards.reduce(
              (sum, c) => sum + (Number.isFinite(c.quantity) ? c.quantity : 1), 0
            );
            return (
              <>
                Showing <strong>{filtered.length}</strong> of {cards.length} entries
                {totalQty !== filtered.length && (
                  <> &nbsp;·&nbsp; <strong>{totalQty}</strong> total cards</>
                )}
                {totalValue > 0 && (
                  <> &nbsp;·&nbsp; <strong>${totalValue.toFixed(2)}</strong> value</>
                )}
                {collectionQty !== totalQty && (
                  <span className="filter-summary-muted">
                    &nbsp;(collection: {collectionQty} cards)
                  </span>
                )}
              </>
            );
          })()}
        </div>
      </div>

      {cards.length === 0 ? (
        <p className="empty-state">
          No cards yet. <Link to="/cards/add">Add your first card</Link>.
        </p>
      ) : filtered.length === 0 ? (
        <p className="empty-state">
          No cards match the current filters.{' '}
          <button type="button" className="link-button" onClick={clearFilters}>
            Clear filters
          </button>{' '}
          to see your full collection.
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
