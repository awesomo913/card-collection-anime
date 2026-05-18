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

const apiClient = {
  getCards, getCard, createCard, updateCard, deleteCard,
  getSealedProducts, getSealedProduct, createSealedProduct, updateSealedProduct, deleteSealedProduct,
  getCollectionValue, getSnapshot, triggerPriceUpdate, getPriceHistory,
  searchCatalog, resolveCatalogUrl,
  exportProfile, importProfile,
  getStatus, getStatusLogs,
  identifyImage, identifyBatch, identifyVideo,
};

export default apiClient;
