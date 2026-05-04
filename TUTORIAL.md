# Card Collection (Anime) — Tutorial
**Last updated:** 2026-05-03

---

## 1. Quickstart

Two terminals, ~60 seconds total. The app uses mock prices out of the box — no API keys needed to start.

**Terminal 1 (backend):**
```bash
cd backend
python -m venv venv && source venv/bin/activate    # macOS/Linux
# venv\Scripts\activate                              # Windows
uv pip install -r requirements.txt
uvicorn main:app --reload
```
You should see `Uvicorn running on http://127.0.0.1:8000`.

**Terminal 2 (frontend):**
```bash
cd frontend
npm install
npm start
```
Browser auto-opens to `http://localhost:3000`. You'll see the dashboard with `Total Value: $0.00 · Single Cards: 0 · Sealed Products: 0` and three nav buttons.

**What now?** Click `+ ADD CARD`, paste a TCGplayer URL into the search box, hit Enter — the app fills in the rest. See "Add a card from a URL" below.

---

## 2. Feature Walkthrough

### Dashboard
- **What it does:** Shows total collection value, item counts, and quick-add buttons.
- **When to use it:** Default landing page — first place you go to see what your collection is worth.
- **How:** Click `DASHBOARD` in the top nav. Counts and total update live after every refresh.
- **Gotchas:** Total Value uses each item's `current_price × quantity`. If `current_price` is null (refresh hasn't run yet), the item contributes $0.

### My Cards
- **What it does:** Lists every single card with its image, set, rarity, condition, and current price. Each tile has a 7-day price sparkline.
- **When to use it:** Browsing inventory or editing a card (right-click → Edit / Delete).
- **How:** Click `MY CARDS` in the nav. Filter by game with the color-coded chips (violet=Magic, gold=Pokémon, crimson=Yu-Gi-Oh!).
- **Gotchas:** Sparklines need at least 2 history rows to draw. New cards show a flat line until the next scheduler tick (default: every 24h, or hit `POST /prices/update` to force one).

### Sealed Products
- **What it does:** Same as My Cards but for booster boxes, packs, decks. Distinct list because sealed pricing is volatile + product types are different.
- **When to use it:** Tracking sealed inventory you're holding for resale or play.
- **How:** Click `SEALED PRODUCTS`. `+ ADD SEALED` to create. Catalog search supports Magic only (Pokemon and YGO public APIs don't carry sealed product — for those, use the URL paste flow with a TCGplayer link directly).

### Price Snapshot
- **What it does:** Shows your collection value broken down by price source (TCGplayer / eBay / CardMarket / mock) plus a 24-hour history bar.
- **When to use it:** When you want to see if eBay vs. TCGplayer is favoring your collection right now, or whether the last refresh moved the needle.
- **How:** Click `PRICE SNAPSHOT`. Bars are color-graded by source.
- **Gotchas:** Sources you haven't configured (no API creds in env) show as `mock`. That's not a bug.

### Backup
- **What it does:** Encrypted export/import of your entire collection (Cards + Sealed + PriceHistory) as a single text blob protected by a password.
- **When to use it:** Moving data between PC and Pi; before a risky migration; periodic safety backup.
- **How:** Settings → Export Profile → enter a password → save the blob to a file. Import: paste the blob → enter the same password → choose "Replace" or "Merge".
- **Gotchas:** Wrong password returns HTTP 400 (no decrypt is possible). The export uses Fernet (cryptography lib) — if you lose the password, the data is unrecoverable.

### Status
- **What it does:** Operational dashboard — server uptime, CPU/memory/disk, scheduler health, DB row counts, last 100 log lines.
- **When to use it:** Confirming the Pi is healthy, debugging a refresh that didn't fire, checking what version is running.
- **How:** Click `STATUS`. The `started_at` field tells you when uvicorn last restarted — useful after deploys.

---

## 3. Common Workflows / Recipes

### Add a card from a URL
**Goal:** Catalog a Yu-Gi-Oh! Dark Magician (Starlight Rare) you just bought.
1. Click `+ ADD CARD`.
2. In the search box, paste the TCGplayer URL: `https://www.tcgplayer.com/product/687196/yugioh-rarity-collection-5-dark-magician-starlight-rare`.
3. The form auto-fills: name = "Dark Magician", set = "Rarity Collection", rarity = "Starlight Rare", price = current TCGplayer market.
4. Set quantity, condition, optional purchase price.
5. Click Save. The card appears in My Cards with its image and a sparkline that'll start filling in on the next refresh.

**Result:** A card row with `external_source = ygoprodeck` and `external_id = 80517377` (or whatever the YGOPRODeck ID is). Future refreshes hit the catalog directly — no name guessing.

### Run on a Raspberry Pi
**Goal:** Set up the app on a Pi so it's always reachable on your LAN.
1. SSH to the Pi (or use a keyboard).
2. `git clone https://github.com/awesomo913/card-collection-anime.git ~/card-collection-anime` (first time) or `cd ~/card-collection-anime && git pull` (subsequent updates).
3. `bash deploy/pi-run-nosudo.sh`. The script bootstraps Node via `fnm` if needed, builds the frontend, and launches uvicorn on port 8000.
4. Find your Pi's LAN IP: `hostname -I`. Open `http://<that-ip>:8000` from any device on the LAN.
5. To run unattended (survive SSH disconnect): `nohup bash deploy/pi-run-nosudo.sh > ~/card.log 2>&1 < /dev/null &`.

**Result:** UI + API live on `http://<pi-ip>:8000`. Visit `/status` to confirm.

### Encrypted backup → restore
**Goal:** Migrate your collection from PC to Pi (or vice versa).
1. On source machine: Settings → Export Profile → set a password → copy the blob to clipboard or save to a file.
2. On destination machine: Settings → Import Profile → paste the blob → enter the same password → click "Replace" (wipes destination) or "Merge".
3. Hit Dashboard — counts should match the source.

**Result:** All Cards + Sealed + PriceHistory rows transferred. The blob is portable text; safe to email yourself.

### Force a price refresh
**Goal:** See updated prices right now instead of waiting for the next scheduler tick.
1. Either click "Refresh prices" in the UI (if shown) or hit `POST http://<host>:8000/prices/update` from curl/Postman.
2. Wait 5-30s depending on collection size and provider count.
3. Refresh My Cards / Snapshot — new prices + history rows are visible.

**Result:** Every item gets a fresh price; one new `PriceHistory` row per source per item.

---

## 4. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| URL paste returns "Could not resolve that URL" | Host not in the supported set, or the catalog API is rate-limiting | Verify host is one of: tcgplayer.com, scryfall.com, pokemontcg.io, db.ygoprodeck.com. Try again after a minute (catalogs throttle). |
| URL returns the wrong card (e.g. "Rare Fish" for a Dark Magician URL) | You're on a pre-`c33c249` build of the resolver | `git pull` and restart. The fix landed in commit `c33c249` (2026-05-03). Check current commit at `/status` (build version) or `git log -1`. |
| Dashboard shows $0 right after import | Prices haven't been refreshed yet | Hit `POST /prices/update` or wait for the next scheduler tick (default 24h). |
| `pytest` hangs or fails with scheduler errors | Background scheduler firing during tests | Always run with `DISABLE_SCHEDULER=1`: `DISABLE_SCHEDULER=1 pytest -v`. |
| Pi `pi-run-nosudo.sh` fails with "command not found: npm" | Pi OS Bookworm doesn't ship npm | The script auto-installs `fnm` + Node 20. If it fails, check `~/.local/share/fnm/` exists and is on PATH. |
| `/profile/import` returns HTTP 400 "Invalid or corrupted backup" | Wrong password (most common) or truncated paste | Re-paste the full blob; double-check the password. |
| Frontend shows blank page after Pi deploy | `frontend/build/` not regenerated since last `git pull` | Re-run `bash deploy/pi-run-nosudo.sh` — it always rebuilds. |
| Sparkline is flat / single dot | < 2 PriceHistory rows for the item | Either wait for more refreshes or trigger `POST /prices/update`. |
| TCGplayer prices missing on URL-imported cards | TCGplayer's `mp-search-api` blocked or returned no `marketPrice` (presale, OOP) | Falls back to per-game catalog price. Manually edit `current_price` if needed. |

---

## 5. FAQ

- **Q: Do I need API keys to start?** A: No. The app uses deterministic mock prices when no provider credentials are set. Add real keys (env vars) when you want live pricing.
- **Q: What games are supported?** A: Magic: The Gathering, Pokémon, Yu-Gi-Oh!. The catalog backends differ per game (Scryfall / PokemonTCG.io / YGOPRODeck).
- **Q: Can I add my own catalog source?** A: Yes — implement the `PriceProvider` Protocol in `backend/providers/base.py` and register it in `backend/providers/registry.py`.
- **Q: Does the app work offline?** A: The UI works; price refreshes need internet. Mock prices won't update without internet either (they're deterministic but the scheduler still tries to call providers).
- **Q: Is the encrypted backup truly recoverable only with the password?** A: Yes — Fernet (AES-128 in CBC + HMAC). No backdoor. Lose the password, lose the data.
- **Q: Can I run this on macOS / Linux / Windows?** A: All three for development. Production deploy scripts assume Linux (Pi) or Android (Termux). Windows production deploy isn't packaged.

---

## 6. Changelog (user-facing)

### 2026-05-03 — v1.4 (catalog hardening)
- **Fixed:** TCGplayer URLs ending in a rarity treatment (`-starlight-rare`, `-ghost-rare`, `-secret-rare`) for Yu-Gi-Oh! cards no longer return the wrong card. Previously, slug parsing degraded to the token `rare` and matched "Rare Fish".
- **Improved:** TCGplayer's own product details now used as the canonical name + price source for URL-pasted cards (cleaner than slug parsing).

### 2026-05-01 — v1.3 (Pi deploy + ops)
- **Added:** `STATUS` page — uptime, CPU/memory, scheduler health, DB counts, last 100 log lines.
- **Added:** Single-port deploy mode — UI + API on one port (mounted from `frontend/build/`).
- **Added:** Pi installer (`pi-run-nosudo.sh`) — works without sudo, bootstraps Node via `fnm`.
- **Added:** Termux installer + guide for running on Android.

### 2026-04-30 — v1.2 (catalog import + backup)
- **Added:** Paste a TCGplayer / Scryfall / PokemonTCG / YGOPRODeck URL → app pulls name, image, set, exact-printing TCGplayer price.
- **Added:** Live catalog search — type a card name, pick from results, save with one click.
- **Added:** Sealed-product catalog search (Magic; Pokemon/YGO require URL paste).
- **Added:** Encrypted profile backup — export/import the whole collection as a password-protected blob.

### 2026-04-29 — v1.1 (CI)
- **Added:** GitHub Actions running backend pytest + frontend build on every push/PR.

### 2026-04-28 — v1.0 (initial release)
- First version: card + sealed inventory, multi-source pricing (TCGplayer/eBay/CardMarket with mock fallback), per-source snapshot, per-item price history with sparkline, neon anime UI.
