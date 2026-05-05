import React, { useEffect, useRef, useState } from 'react';
import api from '../services/api';

/**
 * Live catalog search panel.
 *
 * Props:
 *   game       - 'magic' | 'pokemon' | 'yugioh'
 *   onPick     - (result) => void  result is a CatalogResult dict from the backend
 *   sealed     - boolean: when true, search Magic sealed product instead of singles
 *   autoFocus  - boolean: focus the input on mount (Phase D)
 *
 * Renders a search input + result grid. Debounces keystrokes by 350ms before
 * hitting /catalog/search to keep traffic to the public APIs civilized. Listens
 * for window 'catalog-search-prefill' events so AddCardPage's "Paste URL" button
 * can stuff a URL into this component without prop drilling.
 */
const CatalogSearch = ({ game, onPick, sealed = false, autoFocus = false }) => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [status, setStatus] = useState('idle');
  const [activeIndex, setActiveIndex] = useState(-1);
  const debounceRef = useRef(null);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  // onPick comes from a parent that re-creates it every render. Pin it through a
  // ref so the search effect's dependency list doesn't fire on every keystroke.
  const onPickRef = useRef(onPick);
  useEffect(() => { onPickRef.current = onPick; }, [onPick]);

  // Phase D: external prefill via custom window event. AddCardPage dispatches
  // this when the user clicks "Paste URL" so we don't need prop-drilling.
  useEffect(() => {
    const handler = (e) => {
      const url = e?.detail;
      if (typeof url === 'string' && url) {
        setQuery(url);
        if (inputRef.current) inputRef.current.focus();
      }
    };
    window.addEventListener('catalog-search-prefill', handler);
    return () => window.removeEventListener('catalog-search-prefill', handler);
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const trimmed = (query || '').trim();
    if (!trimmed || trimmed.length < 2) {
      setResults([]);
      setStatus('idle');
      setActiveIndex(-1);
      return;
    }

    const looksLikeUrl = /^https?:\/\//i.test(trimmed);
    setStatus(looksLikeUrl ? 'resolving' : 'searching');

    debounceRef.current = setTimeout(async () => {
      try {
        if (looksLikeUrl) {
          // URL drop: skip search, hit /catalog/resolve and auto-pick the single result.
          const res = await api.resolveCatalogUrl(trimmed);
          if (res.data) {
            setResults([res.data]);
            setStatus('resolved');
            onPickRef.current(res.data);
          } else {
            setResults([]);
            setStatus('empty');
          }
        } else {
          const res = await api.searchCatalog(trimmed, game, { sealed });
          setResults(res.data || []);
          setStatus((res.data || []).length === 0 ? 'empty' : 'idle');
          setActiveIndex(-1);
        }
      } catch (err) {
        console.error(err);
        setStatus(looksLikeUrl ? 'url-error' : 'error');
      }
    }, 350);
    return () => clearTimeout(debounceRef.current);
  }, [query, game, sealed]);

  /**
   * Keyboard nav for the result grid:
   *   ArrowDown / ArrowUp — cycle highlighted result (wraps).
   *   Enter / Space       — pick the highlighted result.
   *   Escape              — clear selection.
   */
  const handleInputKeyDown = (e) => {
    if (results.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % results.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => (i <= 0 ? results.length - 1 : i - 1));
    } else if (e.key === 'Enter' && activeIndex >= 0 && activeIndex < results.length) {
      e.preventDefault();
      onPick(results[activeIndex]);
    } else if (e.key === 'Escape') {
      setActiveIndex(-1);
    }
  };

  return (
    <div className="catalog-search">
      <label className="catalog-search-label">
        {sealed
          ? 'Paste a TCGplayer / Scryfall URL — or search Magic sealed by name'
          : 'Search TCG by name — or paste a Scryfall / TCGplayer / PokemonTCG / YGOPRODeck URL'}
        <input
          ref={inputRef}
          type="text"
          placeholder={
            sealed
              ? 'Paste a TCGplayer URL or search Magic sealed…'
              : `Search ${game} or paste a URL…`
          }
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleInputKeyDown}
          autoFocus={autoFocus}
          aria-autocomplete="list"
          aria-controls="catalog-search-results"
          aria-activedescendant={
            activeIndex >= 0 ? `catalog-result-${activeIndex}` : undefined
          }
        />
      </label>

      {status === 'searching' && <div className="catalog-status">Searching…</div>}
      {status === 'resolving' && <div className="catalog-status">Resolving URL…</div>}
      {status === 'resolved' && <div className="catalog-status">Resolved — autofilled below.</div>}
      {status === 'empty' && <div className="catalog-status">No matches.</div>}
      {status === 'error' && (
        <div className="catalog-status error">
          Search failed. Try again or fill the form manually below.
        </div>
      )}
      {status === 'url-error' && (
        <div className="catalog-status error">
          Couldn't resolve that URL. Check the link or fill the form manually below.
        </div>
      )}

      {results.length > 0 && (
        <ul
          id="catalog-search-results"
          className="catalog-results"
          role="listbox"
          ref={listRef}
        >
          {results.map((r, idx) => (
            <li
              id={`catalog-result-${idx}`}
              key={`${r.external_source}:${r.external_id}`}
              role="option"
              aria-selected={idx === activeIndex}
              className={idx === activeIndex ? 'is-active' : ''}
              tabIndex={0}
              onClick={() => onPick(r)}
              onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onPick(r)}
              onMouseEnter={() => setActiveIndex(idx)}
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
