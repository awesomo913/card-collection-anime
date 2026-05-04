---
public-visible: true   # repo is on awesomo913/card-collection-anime — public GitHub
---

# Card Collection (Anime) — Handoff
**Last updated:** 2026-05-03
**Current owner:** User (primary designer) + Claude (implementation)
**Status:** in-progress — shipping bug fixes, expanding deploy targets

---

## 1. Goals

- Track every card the user owns (Magic, Pokemon, Yu-Gi-Oh!) — singles and sealed product — in one app.
- Pull live prices from TCGplayer/eBay/CardMarket and show how the collection's value is changing over time.
- Accept a paste-of-URL workflow: copy a TCGplayer/Scryfall/PokemonTCG/YGOPRODeck link → app pulls name, image, set, and the *exact printing's* TCGplayer price.
- Run on a home server (Raspberry Pi) so the user can hit it from any device on their LAN.
- Look like an arcade dashboard, not enterprise inventory software.

## 2. Outline (architecture at 30k ft)

- **Backend** — FastAPI + SQLAlchemy + SQLite (Postgres-ready). Three tables: Card, SealedProduct, PriceHistory. Background thread refreshes prices on a configurable interval.
- **Catalog layer** — three free public APIs (Scryfall / PokemonTCG.io / YGOPRODeck) for card metadata + TCGplayer's own product details API for the canonical printing-specific price.
- **Provider layer** — TCGplayer/eBay/CardMarket clients (OAuth2/Browse/OAuth1). Optional — without creds the app uses deterministic mock prices so dev + CI work offline.
- **Frontend** — React 18 + react-router-dom 6, axios for the API. Pages: Dashboard / Cards / Sealed / Snapshot / Backup / Status. Reusable TileCard + Sparkline components.
- **Deploy** — single-port mode (FastAPI mounts `frontend/build/` if present). Targets: dev (`uvicorn --reload` + `npm start`), Pi (`pi-run-nosudo.sh`, no sudo, user-space Node via `fnm`), Android (`termux-run.sh`).
- **CI** — GitHub Actions: backend pytest + frontend build on push/PR.

## 3. Context (why this exists)

The user collects across all three major TCGs and wanted one inventory + valuation app — most existing apps specialize in one game and don't handle sealed product well. The user explicitly wanted a paste-a-URL workflow because typing card names is error-prone for printings/rarities (e.g. there are dozens of "Charizard" cards across sets).

The user picked FastAPI + React because it's the same stack as their other home-server projects. The neon anime theme is a deliberate aesthetic choice — the user wanted it to feel like a side-scroller HUD, not a spreadsheet. The Raspberry Pi target was chosen so the app can run 24/7 without keeping the PC on.

The TCGplayer URL paste flow is the highest-value feature in practice — it eliminates the hardest part of cataloging (mapping a real-world card to the correct printing in the database).

## 4. History (dated, append-only)

### 2026-04-28 — Initial design
- **User's vision:** an inventory app for all three TCGs, sealed-product-aware, with multi-source pricing and a neon anime UI.
- **User's key decisions:** FastAPI + React stack (consistency with other projects); mock price fallback so the app is usable without provider creds; per-game color accents (Magic=violet, Pokémon=gold, Yu-Gi-Oh!=crimson).
- **Claude implemented:** initial backend models + endpoints, frontend pages, mock price provider, 18-test suite.
- **Shipped:** commit `bdbc9f8`.

### 2026-04-30 — Catalog import flow
- **User requested:** "let me paste a TCGplayer URL and have the app figure out the rest."
- **User-designed details:** any of the four catalog hosts should work (TCGplayer, Scryfall, PokemonTCG.io, YGOPRODeck); the resolved card should keep the link to its source so price refreshes hit the exact printing; encrypted backup should ship at the same time so the user can move data between Pi and PC.
- **Claude implemented:** `/catalog/search` + `/catalog/resolve` endpoints, `external_source`+`external_id`+`image_url` columns + Alembic migration, frontend CatalogSearch component, encrypted profile export/import using `cryptography.Fernet`.
- **Verified:** manual paste of one URL per game worked; `/profile/export` round-tripped through `/profile/import` with correct password.

### 2026-05-01 — Pi + status
- **User requested:** "I want this running on the Pi so I don't need my PC on, and I want a status page so I can see if it's healthy."
- **User-designed details:** no sudo (Pi user is restricted); UI and API on the same port (no separate frontend server); status page should show uptime, system metrics, recent log lines, and DB counts.
- **Claude implemented:** `pi-run-nosudo.sh` with `fnm` bootstrap, single-port static mount in FastAPI, `status.py` with in-memory log ring buffer + `/status` + `/status/logs` endpoints, StatusPage in the frontend.
- **Verified:** Pi reachable at `http://<lan-ip>:8000`, `/status` returns expected payload, status page renders.

### 2026-05-03 — Dark Magician URL bug
- **User reported:** pasting `https://www.tcgplayer.com/product/687196/yugioh-rarity-collection-5-dark-magician-starlight-rare` showed "Rare Fish" instead of Dark Magician.
- **Root cause:** the resolver's slug-token shrinking-N-gram fallback degraded to a single token `rare`, and YGOPRODeck `fname=rare` returns the first alphabetical hit ("Rare Fish").
- **User-designed details:** fix the resolver so any URL with a rarity-treatment suffix (`-starlight-rare`, `-ghost-rare`, `-secret-rare`) resolves to the correct card; don't regress the existing tests.
- **Claude implemented:** TCGplayer details API's `productName` is now the primary YGO/Pokemon search query (slug parsing kept as fallback); added `_clean_tcgplayer_product_name()` and `_strip_rarity_suffix_tokens()` helpers; dropped the n=1 single-token fallback; added regression test mirroring the user's URL.
- **Shipped:** commit `c33c249` to `origin/main`.
- **Verified locally:** unit test green; Dark Magician URL probed against the running Pi confirmed *bug still present in production* — Pi was not yet on the new code at time of write.
- **Open:** Pi pull + restart needed to make the fix live; once live, re-probe + run pytest on Pi to confirm.
- **Also:** BREAKDOWN.md + HANDOFF.md + TUTORIAL.md added (project-docs rule was firing repeatedly without these files existing).

## 5. Credit & Authorship

> **The user designed this product.** The user defined goals (multi-TCG inventory + sealed product + paste-a-URL flow + neon anime UI + Pi-deployable), feature priorities (URL resolver hardening came directly from the user reporting a bad result, not from Claude proposing it), UI decisions (color accents, tile layout, sparkline placement), and acceptance criteria (the URL must resolve to the correct printing). Claude (across multiple sessions) implemented the code, tests, and deploy scripts to those specifications. The user reviewed every release and made go/no-go decisions. This is the user's product; AI was a tool.

## 6. Plan (what's next)

- [ ] **Re-deploy the Pi to pick up commit `c33c249`** (Dark Magician fix). Currently still serving pre-fix code.
- [ ] Verify the resolver fix end-to-end against the live Pi (probe the exact failing URL, confirm name = "Dark Magician").
- [ ] Run `pytest` on the Pi (Python 3.13.5 on aarch64 — different from any local CI environment).
- [ ] Decide whether to delete the orphan `card_collection_backend/` + `card_collection_frontend/` predecessor folders on the user's PC.
- [ ] Decide whether to integrate the workspace crash logger (`~/.claude/scripts/crash_logger.py`) into `backend/main.py` per the crash-logger rule.

## 7. Handoff checklist for the next AI

- [ ] Read **Goals** — what this product is FOR.
- [ ] Read **Context** — why it was built this way (Pi target, paste-a-URL flow, neon theme).
- [ ] Read the last 3 entries in **History** — what was just shipped (URL resolver fix, Pi deploy, catalog import flow).
- [ ] Read [`BREAKDOWN.md`](BREAKDOWN.md) — technical architecture.
- [ ] Read [`TUTORIAL.md`](TUTORIAL.md) — how the user actually uses this.
- [ ] Read [`backend/providers/catalog.py`](backend/providers/catalog.py) before touching the URL resolver. Helper functions are intentionally ordered: API-name preferred, slug parsing as fallback, n=1 token fallback removed for safety.
- [ ] Read [`backend/test_api.py`](backend/test_api.py) — the regression test for the Dark Magician bug is at lines 411-491. Don't drop it.
- [ ] Check `git log --oneline -10` — recent commits explain recent intent.
- [ ] If the Pi is involved, hit `http://192.168.1.213:8000/status` first to confirm it's alive and on the expected commit. Pi user is unknown (not `pi`, not `jacob`); ask the human before SSH attempts.
