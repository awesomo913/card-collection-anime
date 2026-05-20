import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || '';

const api = axios.create({ baseURL: API_BASE_URL });

// Cards
export const getCards = () => api.get('/cards/');
export const getCard = (id) => api.get(`/cards/${id}`);
export const createCard = (card) => api.post('/cards/', card);
export const updateCard = (id, card) => api.put(`/cards/${id}`, card);
export const deleteCard = (id) => api.delete(`/cards/${id}`);

// Sealed
export const getSealedProducts = () => api.get('/sealed/');
export const getSealedProduct = (id) => api.get(`/sealed/${id}`);
export const createSealedProduct = (sealed) => api.post('/sealed/', sealed);
export const updateSealedProduct = (id, sealed) => api.put(`/sealed/${id}`, sealed);
export const deleteSealedProduct = (id) => api.delete(`/sealed/${id}`);

// Aggregates
export const getCollectionValue = () => api.get('/collection/value');
export const getSnapshot = () => api.get('/snapshot');
export const triggerPriceUpdate = () => api.post('/prices/update');

// Per-item history (item_type: 'card' | 'sealed')
export const getPriceHistory = (itemType, itemId) =>
  api.get(`/price-history/${itemType}/${itemId}`);

// Live eBay listings for an item (on-demand, button-triggered).
// itemType: 'card' | 'sealed'. Returns { enabled, listings, summary }.
export const getEbayListings = (itemType, id) =>
  api.get(`/items/${itemType}/${id}/ebay`, { timeout: 30000 });

// Live catalog search (game: 'magic' | 'pokemon' | 'yugioh')
export const searchCatalog = (q, game, { limit = 12, sealed = false } = {}) =>
  api.get('/catalog/search', { params: { q, game, limit, sealed } });

// Resolve a catalog URL (Scryfall / TCGplayer / PokemonTCG.io / YGOPRODeck)
// to a single CatalogResult.
export const resolveCatalogUrl = (url) =>
  api.get('/catalog/resolve', { params: { url } });

// Server status (uptime, system metrics, DB counts, scheduler health).
export const getStatus = () => api.get('/status');
export const getStatusLogs = (limit = 100, level) =>
  api.get('/status/logs', { params: { limit, ...(level ? { level } : {}) } });

// Encrypted backup: server returns the cipher-text blob; client downloads it.
export const exportProfile = (password) =>
  api.post('/profile/export', { password }, { responseType: 'text', transformResponse: (x) => x });
export const importProfile = (encrypted, password, replace = true) =>
  api.post('/profile/import', { encrypted, password, replace });

// DeepSeek multimodal identification (server proxy — key never in browser).
export const identifyImage = (file, gameHint) => {
  const fd = new FormData();
  fd.append('file', file);
  const params = gameHint ? { game_hint: gameHint } : {};
  return api.post('/identify/image', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    params,
    // DeepSeek can take 10-30s per image; default axios timeout is too tight.
    timeout: 90000,
  });
};
export const identifyBatch = (files) => {
  const fd = new FormData();
  Array.from(files).forEach((f) => fd.append('files', f));
  return api.post('/identify/batch', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    // Batch is parallel on the server but with N items the wall-clock can
    // still grow. Allow 3 min for a 30-image drop.
    timeout: 180000,
  });
};
export const identifyVideo = (file) => {
  const fd = new FormData();
  fd.append('file', file);
  return api.post('/identify/video', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 180000,
  });
};
// Text-only "search a card by name" — DeepSeek infers the game from the name.
// query + game_hint go as query params (matches the /identify/image style).
// 120s timeout: deepseek-v4-pro does heavy hidden reasoning and has been
// observed taking ~68s on a single query — 60s was too tight and intermittently
// aborted the request before the server responded.
export const identifyText = (query, gameHint) =>
  api.post('/identify/text', null, {
    params: { query, ...(gameHint ? { game_hint: gameHint } : {}) },
    timeout: 120000,
  });

// DeepSeek-powered price forecasting. Server caches results 24h.
export const forecastCard = (id) =>
  api.get(`/forecast/card/${id}`, { timeout: 60000 });
export const forecastSealed = (id) =>
  api.get(`/forecast/sealed/${id}`, { timeout: 60000 });
// Whole-collection batch. Cold-cache run can take minutes on 100+ items; cap
// at 10min and let the UI render a long-running spinner. Cached re-runs are
// near-instant (server's (item, last_history_ts) key fires before any DeepSeek
// call so unchanged items are free).
export const forecastBatch = (items) =>
  api.post('/forecast/batch', { items }, { timeout: 600000 });

// Streaming whole-collection forecast (Server-Sent Events). Calls
// onItem({index, done, total, row}) per finished item and onDone({aggregate,
// duration_seconds, cache_hits, cache_misses, model}) at the end. Uses fetch +
// ReadableStream because axios can't stream incrementally in the browser.
// Throws on transport failure so the caller can fall back to forecastBatch.
export const forecastBatchStream = async (items, { onItem, onDone, signal } = {}) => {
  const resp = await fetch(`${API_BASE_URL}/forecast/batch/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`forecast stream failed: HTTP ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  // SSE frames are separated by a blank line. Buffer chunks and parse whole
  // frames as they arrive.
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const evMatch = frame.match(/^event:\s*(.+)$/m);
      const dataMatch = frame.match(/^data:\s*(.+)$/m);
      if (!evMatch || !dataMatch) continue;
      let payload;
      try { payload = JSON.parse(dataMatch[1]); } catch { continue; }
      if (evMatch[1].trim() === 'item') onItem?.(payload);
      else if (evMatch[1].trim() === 'done') onDone?.(payload);
    }
  }
};

const apiClient = {
  getCards, getCard, createCard, updateCard, deleteCard,
  getSealedProducts, getSealedProduct, createSealedProduct, updateSealedProduct, deleteSealedProduct,
  getCollectionValue, getSnapshot, triggerPriceUpdate, getPriceHistory,
  getEbayListings,
  searchCatalog, resolveCatalogUrl,
  exportProfile, importProfile,
  getStatus, getStatusLogs,
  identifyImage, identifyBatch, identifyVideo, identifyText,
  forecastCard, forecastSealed, forecastBatch, forecastBatchStream,
};

export default apiClient;
