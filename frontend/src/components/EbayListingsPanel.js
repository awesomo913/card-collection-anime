import React, { useState } from 'react';
import api from '../services/api';

/**
 * EbayListingsPanel — live eBay listings for an item, button-triggered.
 *
 * Mounted on CardDetailPage + SealedDetailPage. On-demand only: clicking the
 * button hits the backend which queries the eBay Browse API. Listing links open
 * in a new tab. They come from an external API, so they're treated as untrusted:
 * rel="noopener noreferrer", and the backend already drops any URL that isn't
 * https://*.ebay.com. The link host is shown so the user can see the destination.
 *
 * Props:
 *   itemType   'card' | 'sealed'
 *   itemId     numeric ID of the card/sealed product
 */

const fmtMoney = (n, currency) => {
  if (n == null) return '—';
  if (!currency || currency === 'USD') return `$${Number(n).toFixed(2)}`;
  return `${Number(n).toFixed(2)} ${currency}`;
};

const hostOf = (url) => {
  try { return new URL(url).host; } catch { return ''; }
};

const EbayListingsPanel = ({ itemType, itemId }) => {
  const [data, setData] = useState(null);   // { enabled, listings, summary }
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchListings = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getEbayListings(itemType, itemId);
      setData(res.data);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'eBay lookup failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="ebay-panel">
      <div className="ebay-header">
        <h3>eBay listings</h3>
        <button
          type="button"
          onClick={fetchListings}
          disabled={loading}
          className="primary"
        >
          {loading ? 'Searching eBay…' : data ? 'Refresh eBay' : 'Show eBay listings'}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {!data && !loading && !error && (
        <p className="muted">
          Click <strong>Show eBay listings</strong> to pull current eBay
          listings and prices for this item.
        </p>
      )}

      {data && !data.enabled && (
        <p className="muted">
          eBay pricing isn't configured on the server (no eBay API credentials).
          Add <code>EBAY_CLIENT_ID</code> / <code>EBAY_CLIENT_SECRET</code> on the
          Pi to enable live listings.
        </p>
      )}

      {data && data.enabled && data.listings.length === 0 && (
        <p className="muted">No eBay listings found for this item right now.</p>
      )}

      {data && data.enabled && data.listings.length > 0 && (
        <>
          {data.summary && (
            <div className="ebay-summary muted">
              {data.summary.count} listings · median {fmtMoney(data.summary.median)} ·
              {' '}range {fmtMoney(data.summary.min)}–{fmtMoney(data.summary.max)}
            </div>
          )}
          <ul className="ebay-listings">
            {data.listings.map((l, i) => (
              <li key={i} className="ebay-listing">
                {l.image && (
                  <img src={l.image} alt="" className="ebay-thumb" loading="lazy" />
                )}
                <div className="ebay-listing-meta">
                  <a
                    href={l.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ebay-title"
                  >
                    {l.title}
                  </a>
                  <div className="ebay-sub muted">
                    <span className="ebay-price">{fmtMoney(l.price, l.currency)}</span>
                    {l.condition && <span> · {l.condition}</span>}
                    {hostOf(l.url) && <span> · {hostOf(l.url)}</span>}
                  </div>
                </div>
              </li>
            ))}
          </ul>
          <div className="ebay-disclaimer muted">
            Live eBay marketplace listings — asking prices, not just sold.
            Treat as a rough market signal.
          </div>
        </>
      )}
    </div>
  );
};

export default EbayListingsPanel;
