import React, { useEffect, useRef, useState } from 'react';
import api from '../services/api';

/**
 * Live catalog search panel.
 *
 * Props:
 *   game     - 'magic' | 'pokemon' | 'yugioh'
 *   onPick   - (result) => void  result is a CatalogResult dict from the backend
 *
 * Renders a search input + result grid. Debounces keystrokes by 350ms before
 * hitting /catalog/search to keep traffic to the public APIs civilized.
 */
const CatalogSearch = ({ game, onPick }) => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [status, setStatus] = useState('idle'); // idle | searching | empty | error
  const debounceRef = useRef(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!query || query.trim().length < 2) {
      setResults([]);
      setStatus('idle');
      return;
    }
    setStatus('searching');
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await api.searchCatalog(query.trim(), game);
        setResults(res.data || []);
        setStatus((res.data || []).length === 0 ? 'empty' : 'idle');
      } catch (err) {
        console.error(err);
        setStatus('error');
      }
    }, 350);
    return () => clearTimeout(debounceRef.current);
  }, [query, game]);

  return (
    <div className="catalog-search">
      <label className="catalog-search-label">
        Search TCG by name
        <input
          type="text"
          placeholder={`Search ${game}…`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>

      {status === 'searching' && <div className="catalog-status">Searching…</div>}
      {status === 'empty' && <div className="catalog-status">No matches.</div>}
      {status === 'error' && <div className="catalog-status error">Search failed.</div>}

      {results.length > 0 && (
        <ul className="catalog-results" role="listbox">
          {results.map((r) => (
            <li
              key={`${r.external_source}:${r.external_id}`}
              role="option"
              tabIndex={0}
              onClick={() => onPick(r)}
              onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onPick(r)}
            >
              {r.image_url && (
                <img src={r.image_url} alt={r.name} loading="lazy" className="catalog-thumb" />
              )}
              <div className="catalog-meta">
                <div className="catalog-name">{r.name}</div>
                <div className="catalog-set">{r.set_name || '—'}</div>
                <div className="catalog-price">
                  {r.tcgplayer_price != null
                    ? `$${r.tcgplayer_price.toFixed(2)}`
                    : 'No TCG price'}
                  {r.tcgplayer_price_foil != null && (
                    <span className="catalog-price-foil">
                      &nbsp;/ foil ${r.tcgplayer_price_foil.toFixed(2)}
                    </span>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default CatalogSearch;
