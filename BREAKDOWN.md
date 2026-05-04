# Card Collection (Anime) — Breakdown
**Created:** 2026-04-28
**Location:** `C:\Users\computer\Desktop\AI\card-collection-anime`
**Language/Stack:** Python 3.11+ (FastAPI 0.109+, SQLAlchemy 2.0+, Pydantic 2.6+) + React 18 + react-router-dom 6
**Live deploy:** Raspberry Pi at `http://192.168.1.213:8000` (single-port mode — UI + API on the same uvicorn)

---

## 1. What It Does

Inventory + multi-source price tracking for trading cards. Tracks Magic: The Gathering, Pokémon, and Yu-Gi-Oh! single cards plus sealed product (booster boxes, packs, decks). Pulls live TCGplayer/eBay/CardMarket prices when API credentials are set, otherwise uses deterministic mock prices. Logs every refresh to a per-item history table so each tile shows a 7-day sparkline. Designed to run unattended on a home server (Raspberry Pi) so the user can hit it from any device on the LAN.

## 2. How To Run It

**Backend (development):**
```bash
cd backend
python -m venv venv && source venv/bin/activate    # macOS/Linux
# venv\Scripts\activate                              # Windows
uv pip install -r requirements.txt                   # never use pip directly per workspace rules
uvicorn main:app --reload
```
API at `http://localhost:8000`, OpenAPI docs at `/docs`.

**Frontend (development):**
```bash
cd frontend
npm install
npm start                                            # http://localhost:3000
```

**Single-port deploy (Pi/Termux/anywhere):**
```bash
bash deploy/pi-run-nosudo.sh                         # builds frontend, exec uvicorn on :8000
```
The script bootstraps user-space Node via `fnm` if `npm` is missing, builds the React frontend with `REACT_APP_API_BASE_URL=` (empty → same-origin), then mounts `frontend/build/` from FastAPI. UI + API share one port.

**Tests:**
```bash
cd backend && DISABLE_SCHEDULER=1 pytest -v          # ~19 tests, ~2-3s on a laptop
```

**Required env vars:** none. All are optional:
- `DATABASE_URL` — switch from SQLite to Postgres
- `PRICE_UPDATE_INTERVAL_HOURS` — default 24
- `DISABLE_SCHEDULER=1` — disable background refresh (mandatory for tests)
- `CORS_ORIGINS` — comma-separated extra origins
- `PRICE_SOURCES_ENABLED` — gate provider list (default: all)
- `TCGPLAYER_CLIENT_ID/SECRET`, `EBAY_*`, `CARDMARKET_*` — provider credentials

## 3. Architecture & File Structure

```
card-collection-anime/
├── README.md                         # public-facing overview (also lives at root)
├── BREAKDOWN.md                      # this file
├── HANDOFF.md                        # co-worker / AI handoff doc
├── TUTORIAL.md                       # end-user tutorial
├── .github/workflows/ci.yml          # backend pytest + frontend build on push/PR
├── backend/
│   ├── main.py                       # FastAPI app, all HTTP endpoints, lifespan, CORS, single-port static mount
│   ├── models.py                     # SQLAlchemy: Card, SealedProduct, PriceHistory
│   ├── schemas.py                    # Pydantic v2 request/response models
│   ├── crud.py                       # DB ops, snapshot aggregation, price-history queries
│   ├── database.py                   # SQLAlchemy engine + SessionLocal (SQLite default, Postgres via DATABASE_URL)
│   ├── price_service.py              # update_all_prices() — orchestrates per-source refresh, logs history rows
│   ├── scheduler.py                  # background thread, configurable interval
│   ├── status.py                     # /status endpoint helpers, in-memory log ring buffer
│   ├── profile_backup.py             # encrypted profile export/import (cryptography lib)
│   ├── test_api.py                   # ~19 tests, all in one file (CRUD + snapshot + history + provider gating + URL resolver regression)
│   ├── requirements.txt
│   ├── alembic.ini, alembic/         # migrations: add catalog columns
│   └── providers/
│       ├── base.py                   # PriceProvider Protocol + request_with_backoff helper
│       ├── tcgplayer.py              # OAuth2 client_credentials flow
│       ├── ebay.py                   # Browse API + median-of-listings price
│       ├── cardmarket.py             # OAuth1 (HMAC-SHA1) signed requests
│       ├── registry.py               # env-driven provider selection
│       └── catalog.py                # public catalogs (Scryfall/PokemonTCG.io/YGOPRODeck) + URL resolver
├── frontend/
│   ├── package.json                  # React 18 + react-router-dom 6 + axios 0.27
│   └── src/
│       ├── App.js, index.js
│       ├── App.css                   # neon anime theme (violet=Magic, gold=Pokemon, crimson=YGO)
│       ├── components/
│       │   ├── TileCard.js           # reusable item tile (cards + sealed)
│       │   ├── Sparkline.js          # dependency-free SVG 7-day sparkline
│       │   └── CatalogSearch.js      # live catalog search + URL paste import
│       ├── pages/
│       │   ├── DashboardPage.js      # totals + counts + quick-add buttons
│       │   ├── CardListPage.js, AddCardPage.js
│       │   ├── SealedListPage.js, AddSealedPage.js
│       │   ├── PriceSnapshotPage.js  # per-source totals + 24h history
│       │   ├── SettingsPage.js       # encrypted backup export/import
│       │   └── StatusPage.js         # uptime, system metrics, recent logs
│       ├── data/options.js           # rarity/condition/product-type dropdowns
│       └── services/api.js           # axios client, REACT_APP_API_BASE_URL respected
└── deploy/
    ├── pi-setup.sh                   # one-time Pi provisioning (apt + systemd)
    ├── pi-run-nosudo.sh              # idempotent: git pull → frontend build → exec uvicorn (no sudo)
    ├── termux-run.sh                 # Android Termux variant
    └── TERMUX_GUIDE.md
```

**Data flow:**
- User pastes a TCGplayer/Scryfall/PokemonTCG/YGOPRODeck URL → `GET /catalog/resolve?url=...` → `providers.catalog.resolve_url()` dispatches by host → returns normalized `CatalogResult` (name, set, image, TCGplayer price, rarity, `external_source`, `external_id`).
- User clicks "save" → `POST /cards/` (or `/sealed/`) with the catalog result fields → row written with `external_source` + `external_id` so future refreshes hit the exact catalog ID instead of guessing by name.
- Background scheduler ticks → `update_all_prices()` → for each row with `external_source`, calls `catalog.fetch_tcgplayer_price()`; for plain rows, calls `price_service` providers (TCGplayer/eBay/CardMarket) → writes a `PriceHistory` row per source per item.
- `GET /snapshot` aggregates per-source totals across the whole collection. `GET /price-history/{type}/{id}` returns the per-item time series the sparkline draws.

## 4. Key Decisions & Why

- **TCGplayer's own product API is the canonical price source for URL-pasted items.** Per-game catalogs (Scryfall/PokemonTCG.io/YGOPRODeck) return aggregate prices that don't reflect the specific printing the URL points at. We hit `mp-search-api.tcgplayer.com/v1/product/{id}/details` (the same endpoint the public product page uses, no auth) and pull `marketPrice` + `productName` + `rarityName`. See [`backend/providers/catalog.py:92-116`](backend/providers/catalog.py:92).
- **TCGplayer `productName` beats slug parsing for YGO/Pokemon search.** TCGplayer URL slugs like `...starlight-rare` end in rarity-treatment tokens that aren't part of the card name. The previous resolver did shrinking-N-gram search on slug tokens and would degrade to the single token `rare` → YGOPRODeck `fname=rare` returned "Rare Fish" alphabetically. Fix at `_resolve_tcgplayer_url()` lines 180-247 + helpers `_clean_tcgplayer_product_name()` and `_strip_rarity_suffix_tokens()`.
- **Catalog linkage (external_source, external_id) over fuzzy refresh.** Without it, every refresh re-searches the catalog by name, which is slow + noisy + occasionally wrong. With it, refresh is one direct API call to a known ID. Stored on both Card and SealedProduct.
- **Single-port deploy.** Frontend build directory is mounted from FastAPI as a final fallback (`StaticFiles(html=True)` after all explicit API routes). Lets Pi/Termux serve UI + API from one process on one port. See `main.py:234-236`.
- **In-memory log ring buffer for `/status/logs`.** No external log shipping needed for a home-server use case. Buffer holds the last N records, served as JSON. See `status.py`.
- **Encrypted profile backup.** Exports the entire collection (Cards + Sealed + PriceHistory) as a single password-protected text blob. Uses `cryptography` (Fernet AEAD). See `profile_backup.py`.
- **Mock price fallback when no provider creds.** `price_service` checks `PRICE_SOURCES_ENABLED` + provider credentials. Missing creds → deterministic mock prices (hash-based per item). Lets `pytest` and offline dev work without secrets.
- **Pi runner uses `fnm` for user-space Node.** Pi OS Bookworm doesn't ship npm by default, and the deploy is sudo-less. `fnm` is a single static binary that drops Node into `~/.local/share/fnm/`.

## 5. Development Log

### 2026-04-28 — Initial creation
- Built backend: Card + SealedProduct + PriceHistory models, CRUD, snapshot aggregation, mock price provider.
- Built frontend: Dashboard, CardList, AddCard, SealedList, AddSealed, PriceSnapshot pages with neon anime theme.
- Background scheduler for periodic refresh.
- 18 tests covering CRUD, snapshot shape, history logging, mock determinism, provider gating.

### 2026-04-29 — CI hardening
- Added httpx as explicit dep (FastAPI TestClient runtime requirement).
- GitHub Actions: backend pytest + frontend build on push/PR.

### 2026-04-30 — Catalog search + URL paste import
- Added `/catalog/search` and `/catalog/resolve` backed by Scryfall (Magic), PokemonTCG.io (Pokemon), YGOPRODeck (YGO).
- Frontend CatalogSearch component: live autocomplete + paste-a-URL flow.
- Card/SealedProduct models gained `external_source`, `external_id`, `image_url` columns (Alembic migration `a1b2c3d4e5f6_add_catalog_columns`).
- Sealed-product catalog search added (Magic only — Pokemon/YGO public APIs only carry singles).
- YGO URL resolver picks the printing matching the slug's set tokens.
- Encrypted profile backup (`/profile/export`, `/profile/import`).
- TCGplayer's own product API made authoritative for URL-pasted prices.

### 2026-05-01 — Operations + Pi deploy
- `/status` page: uptime, system metrics, scheduler health, DB counts.
- `/status/logs` in-memory ring buffer.
- `pi-run-nosudo.sh`: idempotent git pull → frontend build → exec uvicorn, with `fnm` bootstrap when npm is missing.
- Single-port mode: FastAPI serves the React build when `frontend/build/` exists.
- Termux variant `termux-run.sh` + `TERMUX_GUIDE.md`.

### 2026-05-03 — URL resolver hardening (Dark Magician bug)
- **Reported by user:** pasting `https://www.tcgplayer.com/product/687196/yugioh-rarity-collection-5-dark-magician-starlight-rare` resolved to "Rare Fish" instead of Dark Magician.
- **Root cause:** `_split_slug` extracted `dark magician starlight rare`; the n=1 fallback then searched YGOPRODeck for `fname=rare`, which returns the first alphabetical card containing "rare" → "Rare Fish".
- **Fix (commit `c33c249`):** TCGplayer details API's `productName` is now the primary YGO/Pokemon search query. Slug parsing is fallback only. Added `_strip_rarity_suffix_tokens()` to drop trailing rarity words. Dropped n=1 fallback (single-token YGO searches are too noisy).
- Added regression test mirroring the user's exact URL: `backend/test_api.py:411-491`.
- BREAKDOWN.md + HANDOFF.md + TUTORIAL.md created (this entry).
- **Pending verification:** Pi at 192.168.1.213 must be re-deployed (still on pre-fix code as of this entry).
