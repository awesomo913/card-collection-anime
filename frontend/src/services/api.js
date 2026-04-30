import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000';

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
export const searchCatalog = (q, game, limit = 12) =>
  api.get('/catalog/search', { params: { q, game, limit } });

// Resolve a catalog URL (Scryfall / TCGplayer / PokemonTCG.io / YGOPRODeck)
// to a single CatalogResult.
export const resolveCatalogUrl = (url) =>
  api.get('/catalog/resolve', { params: { url } });

const apiClient = {
  getCards, getCard, createCard, updateCard, deleteCard,
  getSealedProducts, getSealedProduct, createSealedProduct, updateSealedProduct, deleteSealedProduct,
  getCollectionValue, getSnapshot, triggerPriceUpdate, getPriceHistory,
  searchCatalog, resolveCatalogUrl,
};

export default apiClient;
