<!-- claude-backend:generated:start -->
# card-collection-anime

## Overview

- **Files**: 72 (.py (34), .js (26), .md (9), .json (3))
- **Entry points**: `backend/main.py`, `tools/camera_check.py`
- **Key files**: `README.md`, `.gitignore`

## Structure

```
backend/  (33 files)
  alembic/  (5 files)
  providers/  (8 files)
deploy/  (1 files)
docs/  (4 files)
  superpowers/  (3 files)
frontend/  (28 files)
  src/  (26 files)
tools/  (1 files)
```

## Conventions

- Type hints are used on some functions
- Use specific exception types in except clauses
- Use `logging.getLogger(__name__)` for all logging
- Absolute imports preferred

## Modules

- `backend/alembic/env.py` -- Alembic environment — uses same engine and metadata as FastAPI
- `backend/alembic/versions/306501e4286b_initial_models.py` -- initial_models
- `backend/alembic/versions/a1b2c3d4e5f6_add_catalog_columns.py` -- add catalog linkage columns to cards and sealed_products
- `backend/alembic/versions/b2c3d4e5f6a7_add_tcgplayer_product_id.py` -- add tcgplayer_product_id column to cards and sealed_products
- `backend/alembic/versions/c3d4e5f6a7b8_backfill_tcgplayer_source.py` -- backfill external_source=tcgplayer for rows with tcgplayer_product_id
- `backend/alert_logic.py` -- Pure price-alert evaluation — no DB, no I/O, fully unit-testable
- `backend/camera_service.py` -- Raspberry Pi camera capture for the /scan card scanner
- `backend/crud.py`
- `backend/database.py` -- SQL engine and declarative base — shared by FastAPI and Alembic
- `backend/forecast_service.py` -- DeepSeek-powered short-term price forecasting for cards + sealed
- `backend/identify_service.py` -- Orchestration layer for the /identify endpoints
- `backend/main.py` [entry]
- `backend/models.py`
- `backend/price_service.py` -- Price aggregation across configured providers, with deterministic mock fallback
- `backend/profile_backup.py` -- Encrypted collection backup — export to / restore from a password-protected
- `backend/providers/base.py` -- Provider abstraction. Each marketplace implements PriceProvider
- `backend/providers/cardmarket.py` -- CardMarket client — OAuth1 (MKM-Server-Sig)
- `backend/providers/catalog.py` -- Public catalog search across the three games
- `backend/providers/deepseek.py` -- DeepSeek multimodal client — image identification for the /identify endpoints
- `backend/providers/ebay.py` -- eBay client — Browse API
- `backend/providers/registry.py` -- Provider registry and selection
- `backend/providers/tcgplayer.py` -- TCGPlayer client — OAuth2 client_credentials flow with token caching
- `backend/rarity_cv.py` -- Deterministic OpenCV measurements of a card's foil behaviour
- `backend/rarity_service.py` -- Rarity decision layer — the judgment half of the hybrid rarity engine
- `backend/scan_service.py` -- Orchestrator for the /scan card scanner
- `backend/scheduler.py` -- Background price-refresh scheduler
- `backend/schemas.py`
- `backend/status.py` -- Server status helpers: in-memory log ring buffer + system metrics
- `backend/test_alert_logic.py` -- Pure unit tests for the price-alert threshold logic (no DB)
- `backend/test_api.py` -- End-to-end tests for the Card Collection API
- `backend/test_scan.py` -- Tests for the /scan card scanner: rarity-aware pricing, the rarity decision
- `backend/test_watchlist.py` -- End-to-end tests for the watchlist / price-alert endpoints
- `tools/camera_check.py` -- Camera Check — a one-click "does the Pi camera load?" self-test [entry]

<!-- claude-backend:generated:end -->
