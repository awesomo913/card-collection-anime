# Feature: Price-History Alerts

**Date:** 2026-05-23
**Builds on:** the existing per-source `PriceHistory` model + `Sparkline` component.

## What shipped

A per-item price-alert watchlist, layered onto the existing price-history plumbing — no new dependencies.

- **Watch a card or sealed product** for a price move: direction (**drop** / **rise** / **either**) and a **percent threshold** measured from a baseline (defaults to the item's current price).
- **Live alert feed** on the Dashboard — the triggered, non-muted watches, each showing the move %, baseline → current price, and a deep-link to the item.
- **Acknowledge / dismiss** an alert: it mutes until the price recovers out of the trigger zone and **re-crosses** the threshold (no nag loop, no stale alerts).
- **Local persistence** in the existing SQLite DB (a `watchlist` table) — watches survive restarts.
- **Neon-anime styling** reusing the `:root` palette tokens (`--neon-pink`, `--glow-cyan`, `--glass`, …).

## How it works

**Backend (FastAPI + SQLAlchemy):**
- `models.WatchlistEntry` — one row per (item_type, item_id): direction, threshold_pct, baseline_price, muted, note. Auto-created by the existing `create_all` bootstrap (no migration needed).
- `alert_logic.py` — **pure** threshold math (`is_triggered`, `pct_change`, `build_alert`), no DB, fully unit-tested.
- `crud.evaluate_watchlist` — pulls the latest price per item (price_history → falls back to the item's `current_price`) and runs the pure logic; auto-re-arms a muted entry once it recovers.
- Endpoints: `POST /watchlist`, `GET /watchlist`, `DELETE /watchlist/{id}`, `POST /watchlist/{id}/ack`, `GET /alerts`.

**Frontend (React):**
- `services/api.js` — `getWatchlist`, `addWatch`, `deleteWatch`, `ackAlert`, `getAlerts`.
- `components/WatchControl.js` — the per-item watch form (on Card + Sealed detail pages, beside the forecast panel).
- `components/AlertPanel.js` — the Dashboard alert feed (renders nothing when there are no alerts, so it never clutters the page).

## Tests

- `backend/test_alert_logic.py` — 11 pure tests (drop/rise/either, exact-threshold inclusivity, mute/re-arm, bad inputs).
- `backend/test_watchlist.py` — 4 endpoint tests (add/list/delete, 404, fire→ack→re-arm cycle, baseline default).
- Full backend suite: **168 passed, 1 skipped** (also fixed a pre-existing test-isolation bug where `test_api.py` and the new file both set `DATABASE_URL` at import time — now set per-fixture).

## Design choices

- **Alerts are computed live, not stored.** Only the *watch config* is persisted. This avoids a stale-alert table and a background job; `GET /alerts` always reflects the latest price.
- **One watch per item.** Re-watching updates the existing config (and re-arms it) rather than stacking duplicates.
- **Mute, don't delete, on dismiss.** Dismiss sets `muted=True`; the entry re-arms automatically when the price leaves the trigger zone, so a recurring move alerts again.

## Note on TCGValueTracker (resolves audit item #9)

TCGValueTracker was confirmed to be **part of this project**, not a separate repo — there is no standalone TCGValueTracker codebase on disk. The price-tracking responsibility it implied lives here, in `price_service.py` + `PriceHistory` + this alerts feature. **Recommendation: keep it unified here.** A separate value-tracker would duplicate the price-history model, the provider layer (`providers/`), and the snapshot/forecast endpoints already in this backend. If a dedicated "portfolio value over time" view is wanted, it belongs as another page in this app reading the same `PriceHistory`, not a fork.
