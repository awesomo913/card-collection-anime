# Design: Four TCG Tracker Features

**Date:** 2026-05-20
**Project:** card-collection-anime (FastAPI + React, single-port uvicorn on Pi @ 192.168.1.221:8000)
**Author:** session pickup from `2026-05-20-tcg-deeplink-grouping-session`

## Summary

Four additive features. None require schema-destructive changes or a manual
migration. All degrade gracefully when partner-API credentials are absent —
the code ships and works identically whether or not TCGPlayer/eBay keys are
live on the Pi; only the *data shown* differs.

1. **Non-Magic sealed name search** — let Pokémon/Yu-Gi-Oh! sealed products
   resolve by name (today they return nothing).
2. **Streaming forecast progress** — real per-item progress for `/forecast/batch`
   instead of a guessed ETA bar.
3. **Price tiers in history** — capture TCGPlayer low/mid/high/market, not just one.
4. **eBay listings button** — surface live eBay listings + clickable links on detail pages.

## Credential context (the linchpin)

- Confirmed live: `DEEPSEEK_API_KEY` (diary).
- Unknown: `TCGPLAYER_CLIENT_ID/SECRET`, `EBAY_CLIENT_ID/SECRET`, `EBAY_OAUTH_TOKEN`.
- SSH probe of the Pi was denied by the permission classifier; not blocking
  because every feature degrades gracefully.
- Real-data dependency map:
  - Feature 1 (sealed search): DeepSeek (have it) + optional eBay enrich. **No TCGPlayer dependency.**
  - Feature 3 (tiers): TCGPlayer only. Tiers stay `NULL` until TCGPlayer keys exist; non-tier behavior unchanged.
  - Feature 4 (eBay button): eBay. Button shows "eBay not configured" if keys absent.

---

## Feature 1 — Non-Magic sealed name search

### Current behavior
`providers/catalog.py::search(query, game, sealed=True)` returns `[]` for
Pokémon/YGO (lines 35-36): Scryfall is Magic-only; pokemontcg.io/ygoprodeck
carry singles, not sealed. So `CatalogSearch` (frontend) shows no results for
non-Magic sealed; the user must paste a TCGplayer URL.

### Approach (user chose: DeepSeek-normalize + eBay-enrich, layered)
New resolution chain inside `catalog.search` when `sealed=True and game in {pokemon, yugioh}`:

1. **TCGPlayer catalog search** *(if `TCGPLAYER_*` creds present)* — authoritative;
   returns canonical name/set/`tcgplayer_product_id`/price. Reuses the existing
   TCGPlayer token + category-search machinery (`providers/tcgplayer.py`).
2. **DeepSeek normalize** *(fallback, needs only DeepSeek)* — send the raw query to a
   new `identify_service.normalize_sealed(query, game)` that returns
   `{name, set_name, game, product_type, confidence}`. Gives a clean entry to autofill.
3. **eBay enrich** *(optional, if eBay creds)* — query eBay Browse with the normalized
   name to attach a representative price + thumbnail to the result tile.

Each result is a normalized `CatalogResult` dict (same shape `CatalogSearch`
already renders). When the source is DeepSeek/eBay (no canonical catalog id),
`external_source` is set to `"deepseek"` / `"ebay"` and `external_id` is null —
on pick, the form autofills name/set/game/product_type/image, and price refreshes
fall through to the existing name-based eBay aggregation (already works).

### Components touched
- `backend/providers/catalog.py` — new `_search_tcgplayer_sealed()` + chain in `search()`.
- `backend/identify_service.py` — new `normalize_sealed(query, game) -> dict`.
- `backend/providers/ebay.py` — new `fetch_listings()` (shared with Feature 4) used for enrich.
- Frontend: `CatalogSearch.js` + `AddSealedPage.js` copy updates (drop "Magic only" hint).
- No schema change.

### Error handling
- Each tier wrapped; failures fall to the next. Empty chain → `[]` (today's behavior, no regression).
- DeepSeek timeout reuses the 120s client timeout already set for `identifyText`.
- Low-confidence DeepSeek result (< 0.4) is still returned but flagged so the tile can show "best guess".

---

## Feature 2 — Streaming forecast progress

### Current behavior
`POST /forecast/batch` (main.py:599) runs all items in a ThreadPoolExecutor,
buffers every row via `as_completed` (line 673), returns one JSON blob at the
end. `ForecastAllPage.js` shows an *estimated* elapsed/total bar — not real progress.

### Approach
Add `POST /forecast/batch/stream` returning a `StreamingResponse`
(`media_type="text/event-stream"`). The generator runs the *same* per-item
work and yields one SSE event per finished item as `as_completed` produces it,
then a terminal `aggregate` event:

```
event: item
data: {"index": 0, "row": {...BatchForecastResultRow...}, "done": 3, "total": 26}

event: done
data: {"aggregate": [...], "duration_seconds": ..., "cache_hits": ..., "cache_misses": ..., "model": ...}
```

No job-state storage (stateless stream). The existing blocking endpoint stays
for backward compat + tests.

### Components touched
- `backend/main.py` — new `forecast_batch_stream` endpoint; factor the per-item
  body out of `forecast_batch` into a shared `_run_forecast_row()` so both share logic.
- `frontend/src/services/api.js` — `forecastBatchStream(items, onItem, onDone)` using
  `fetch` + `ReadableStream` reader (axios doesn't stream in-browser cleanly).
- `frontend/src/pages/ForecastAllPage.js` — consume the stream; fill rows live; show
  real "N/total" + per-item ticks; keep aggregate banner.

### Error handling
- Per-item errors already carry an `error` field per row — streamed the same way.
- Stream abort (user navigates away) → reader cancelled; server generator exits on disconnect.
- If streaming fetch fails outright, fall back to the blocking endpoint once (graceful).

---

## Feature 3 — Price tiers in PriceHistory

### Current behavior
`PriceHistory` (models.py:67) has a single `price` column. TCGPlayer's pricing
entry already exposes `lowPrice/midPrice/highPrice/marketPrice`
(tcgplayer.py:110) but the provider keeps only one
(`marketPrice or midPrice or lowPrice`).

### Approach
1. Add four nullable columns to `PriceHistory`: `price_low`, `price_mid`,
   `price_high`, `price_market` (`Float`, nullable). Boot self-heal
   (main.py:30-127) auto-`ALTER`s them — **no manual migration**.
2. `ProviderResult` already has `raw`; add an optional typed `tiers: Optional[dict]`
   field (frozen dataclass) so providers can pass tiers without abusing `raw`.
3. TCGPlayer provider populates `tiers={low,mid,high,market}` from the pricing entry.
4. `crud.log_price_history(...)` gains optional tier kwargs; `price_service`
   passes them through when persisting.
5. **Confidence lift:** `forecast_service` reads the latest tier spread
   `(high-low)/mid` as a volatility signal feeding the existing confidence rubric.
   This is the "bigger lift" the diary flagged.

### Components touched
- `backend/models.py` — 4 nullable columns.
- `backend/providers/base.py` — `ProviderResult.tiers` field.
- `backend/providers/tcgplayer.py` — populate tiers.
- `backend/crud.py` — `log_price_history` tier kwargs.
- `backend/price_service.py` — thread tiers through `_persist_*`.
- `backend/forecast_service.py` — optional spread-based volatility signal in rubric.
- `backend/schemas.py` — expose tiers on price-history read model.

### Error handling
- Tiers are best-effort: any missing/null tier stays null; forecast rubric only
  uses the spread when all of low/mid/high present.
- Other providers (eBay/CardMarket) leave tiers null — expected.

---

## Feature 4 — eBay listings button (detail pages only)

### Current behavior
`EbayProvider.fetch()` (ebay.py:74) fetches ≤20 item summaries, computes a
median, and **discards the individual listings + `itemWebUrl`s**. No way to see
the actual listings.

### Approach
1. New `EbayProvider.fetch_listings(query, limit=10) -> list[dict]` returning per
   listing: `title, price, currency, condition, url (itemWebUrl), image, buying_option`.
   `fetch()` (median) stays untouched — refactor the shared HTTP/query-build into a
   private helper both call.
2. New endpoint `GET /items/{item_type}/{item_id}/ebay` → loads the stored
   card/sealed, builds a `PriceQuery` from its identity fields, calls
   `fetch_listings`, returns `{enabled, listings, summary:{count,median,min,max}}`.
   `enabled=false` when eBay creds absent.
3. Frontend: "Show eBay listings" button on `CardDetailPage.js` + `SealedDetailPage.js`.
   Click → fetch → expandable panel of listing rows. Each row is a clickable link.

### Security (untrusted external content)
- eBay `itemWebUrl` values are **data**, rendered as `<a target="_blank"
  rel="noopener noreferrer">` — never auto-followed, never executed.
- URLs displayed verbatim; host shown so the user sees where a link goes.
- Backend validates each listing has a parseable `https://*.ebay.com` URL before
  including it; drops anything else.

### Components touched
- `backend/providers/ebay.py` — `fetch_listings()` + shared helper.
- `backend/main.py` — `GET /items/{item_type}/{item_id}/ebay`.
- `backend/schemas.py` — `EbayListing`, `EbayListingsResponse`.
- `frontend/src/services/api.js` — `getEbayListings(itemType, id)`.
- `frontend/src/pages/CardDetailPage.js`, `SealedDetailPage.js` — button + panel.
- `frontend/src/App.css` — listing-panel styles.

### Error handling
- Creds absent → `{enabled:false}`; button renders "eBay pricing not configured".
- eBay API failure → empty listings + a friendly "no eBay results" state.
- Rate-limit (429) → existing `request_with_backoff` handles retry; on terminal
  failure the panel shows "try again".

---

## Cross-cutting

- **Testing:** every backend change gets pytest coverage (TDD). Mock the eBay/
  TCGPlayer/DeepSeek HTTP at the `request_with_backoff` / client boundary —
  never hit live partner APIs in tests. Target: keep suite green (currently 81 pass / 1 skip).
- **No new external runtime deps** — `requests`, FastAPI `StreamingResponse`,
  React `fetch` are all already available.
- **Deploy:** Pi pull + nohup restart per diary; frontend rebuilds on Pi.
  Verify live with playwright against `http://192.168.1.221:8000`.
- **Build order (per workspace Decoupled-Dev rule):** backend first (pure
  functions returning dicts, pytest-verified), then wire frontend.
- **Logging:** reuse the app's existing `logging` + status ring buffer; no new
  logger framework.

## Out of scope (explicitly)
- Nightly auto-forecast (user chose manual-only).
- Capturing tiers from non-TCGPlayer sources.
- eBay button on list tiles (chose detail-only for cost control).
- TCGPlayer partner-API onboarding (separate, user-side task).

## Build sequence
1. Feature 3 (schema + tiers) — smallest, unblocks forecast signal.
2. Feature 4 (eBay listings) — `fetch_listings` is reused by Feature 1.
3. Feature 1 (sealed search) — depends on `fetch_listings` + new DeepSeek normalize.
4. Feature 2 (streaming forecast) — independent; can interleave.
Each feature: backend + tests green → frontend wire → live verify → next.
