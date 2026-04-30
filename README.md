# Card Collection — Anime Edition

Inventory + multi-source price tracking for **Magic: The Gathering**, **Pokémon**, and **Yu-Gi-Oh!** — single cards and sealed product. FastAPI backend, React frontend, neon-anime UI.

![CI](https://github.com/awesomo913/card-collection-anime/actions/workflows/ci.yml/badge.svg)

## Features

- **Inventory**: track cards (foil, signed, condition, quantity, set, rarity) and sealed product (booster boxes, packs, decks).
- **Multi-source pricing**: pulls per-source prices from **TCGPlayer**, **eBay**, and **CardMarket** when API credentials are configured. Falls back to deterministic mock prices for offline dev.
- **Price snapshot**: aggregate per-source totals across the whole collection in one view.
- **Per-item price history**: every refresh logs a `PriceHistory` row per source. Each tile shows a 7-day sparkline.
- **Background scheduler**: refreshes prices on a configurable interval (env: `PRICE_UPDATE_INTERVAL_HOURS`).
- **Anime UI**: tile-based grid, neon glow, game-color accents (Magic=violet, Pokémon=gold, Yu-Gi-Oh!=crimson), animated bar charts.

## Project layout

```
backend/                    FastAPI + SQLAlchemy + Pydantic v2
  models.py                 Card, SealedProduct, PriceHistory
  schemas.py                Pydantic request/response models
  crud.py                   DB ops, snapshot aggregation, history queries
  price_service.py          Provider aggregation + mock fallback
  providers/                Per-marketplace clients
    base.py                 PriceProvider Protocol + retry/backoff
    tcgplayer.py            OAuth2 client_credentials flow
    ebay.py                 Browse API + median-of-listings price
    cardmarket.py           OAuth1 (HMAC-SHA1) signed requests
    registry.py             Env-driven provider selection
  scheduler.py              Background thread, configurable interval
  test_api.py               18 tests covering CRUD, snapshot, history, providers
frontend/                   React 18 + react-router-dom 6
  src/components/
    TileCard.js             Reusable item tile (cards + sealed)
    Sparkline.js            Dependency-free SVG sparkline
  src/pages/                Dashboard, Cards, Sealed, PriceSnapshot, Add forms
  src/services/api.js       Axios client
  src/App.css               Anime theme: dark base, neon accents, glow
.github/workflows/ci.yml    Backend pytest + frontend build on push/PR
```

## Run locally

### Backend

```bash
cd backend
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
uvicorn main:app --reload
```

API docs auto-served at `http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
npm install
npm start
```

Frontend: `http://localhost:3000`.

## Environment variables

All are optional. Without provider credentials the app uses deterministic mock prices.

```bash
# Switch DB to Postgres (default: sqlite at backend/card_collection.db)
DATABASE_URL=postgresql://user:pass@host:5432/card_collection

# Background scheduler
PRICE_UPDATE_INTERVAL_HOURS=24
DISABLE_SCHEDULER=1   # disables auto-refresh (useful for tests)

# CORS additions (comma-separated)
CORS_ORIGINS=https://my-frontend.example.com

# Provider gate (comma-separated subset of: TCGPlayer,eBay,CardMarket)
PRICE_SOURCES_ENABLED=TCGPlayer,eBay,CardMarket

# TCGPlayer (OAuth2 client_credentials)
TCGPLAYER_CLIENT_ID=...
TCGPLAYER_CLIENT_SECRET=...

# eBay (Browse API; either a static OAuth token OR client_id+secret to mint one)
EBAY_OAUTH_TOKEN=...
EBAY_CLIENT_ID=...
EBAY_CLIENT_SECRET=...
EBAY_MARKETPLACE_ID=EBAY_US

# CardMarket (OAuth1)
CARDMARKET_APP_TOKEN=...
CARDMARKET_APP_SECRET=...
CARDMARKET_ACCESS_TOKEN=...
CARDMARKET_ACCESS_SECRET=...
```

## Tests

```bash
cd backend
DISABLE_SCHEDULER=1 pytest -v
```

18 tests cover CRUD for cards and sealed, snapshot shape, snapshot-after-update, history logging, mock determinism, and provider gating.

## API

| Method | Path                                       | Description                          |
|-------:|--------------------------------------------|--------------------------------------|
| GET    | `/`                                        | Health                               |
| GET    | `/cards/`, `/sealed/`                      | List items                           |
| POST   | `/cards/`, `/sealed/`                      | Create                               |
| GET    | `/cards/{id}`, `/sealed/{id}`              | Read                                 |
| PUT    | `/cards/{id}`, `/sealed/{id}`              | Update (re-fetches price if relevant)|
| DELETE | `/cards/{id}`, `/sealed/{id}`              | Delete                               |
| GET    | `/collection/value`                        | Sum of `current_price × quantity`    |
| POST   | `/prices/update`                           | Trigger a refresh of all prices      |
| GET    | `/snapshot`                                | Per-source totals + 24h history      |
| GET    | `/price-history/{item_type}/{item_id}`     | Full history for one item            |

## License

MIT
