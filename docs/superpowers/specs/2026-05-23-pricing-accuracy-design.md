# Design: Pricing Accuracy (Chunk 1)

**Date:** 2026-05-23
**Project:** card-collection-anime
**Status:** approved (audit + design forks confirmed with user 2026-05-23)

## Summary

Two pricing bugs were found once the eBay Production API went live. Both come
from the same root cause — the eBay query carries too little identity, so it
either matches the wrong things or never runs at all.

1. **eBay median is rarity-blind.** `PriceQuery` has no `rarity` field, so
   `EbayProvider._search_items` builds its query from name + set + foil only.
   Dark Magician *Starlight* and Dark Magician *Ultra* therefore get the
   identical all-rarities median (~$5.35). TCGPlayer is rarity-correct
   (per-printing product IDs: Starlight $8.11 vs Ultra $0.94), so blending the
   rarity-blind eBay number into `current_price` corrupts it — Ultra $0.94 is
   shown as ~$3.14 (inflated 3×), Starlight $8.11 is dragged down to ~$6.73.

2. **Sealed pulls no eBay.** All 13 sealed products carry only
   `{TCGPlayer: X}` in `price_sources`. `_resolve_sealed_prices_detailed`
   returns the catalog TCGPlayer price and then guards the eBay path behind
   `if not prices:` — so once the catalog price lands, eBay never runs. Cards
   don't have this bug: `fetch_card_prices_all_sources_detailed` layers live
   providers *on top of* the catalog price.

The fix makes the eBay query rarity-aware (with a graceful fallback for eBay's
inconsistent rarity tagging), runs eBay on top of the catalog price for sealed
too, and surfaces both a pure-TCGPlayer figure and the blended figure in the UI
so the user always sees the clean per-rarity number alongside the blend.

## Confirmed decisions

- **eBay rarity-aware, with fallback.** Add `rarity` to `PriceQuery`; thread the
  card's rarity through `fetch_card_prices_all_sources_detailed`;
  `_search_items` appends it to the query. If the rarity-scoped search returns
  too few results, fall back to the name+set query (eBay rarity tags are
  inconsistent — a too-narrow query that returns 1 junk listing is worse than
  the broad median).
- **eBay on sealed.** `_resolve_sealed_prices_detailed` runs eBay on top of the
  catalog TCGPlayer price (merge), not return-early.
- **Both figures.** Tile + detail show TWO headline numbers: "TCGPlayer"
  (per-rarity, exact) and "Blended" (TCG+eBay). `price_sources` already carries
  the components `{TCGPlayer, eBay}`; `current_price` stays the blend.
- **Sequencing.** Chunk 1 (pricing) first — prices are wrong *now*. Chunk 2
  (variant button) after.

---

## 1. `PriceQuery.rarity` (`providers/base.py`)

`PriceQuery` is a `frozen` dataclass (line 15). Add one field at the end:

```python
rarity: Optional[str] = None  # cards only; sealed has no single rarity
```

The default keeps every existing construction valid — this is purely additive,
no caller is forced to change.

## 2. Thread rarity through the card path (`price_service.py`)

- **`fetch_card_prices_all_sources_detailed`** (line 97): add
  `rarity: Optional[str] = None` to the signature; build the query as
  `PriceQuery(name=name, set_name=set_name, game=game, is_foil=is_foil, rarity=rarity)`
  (line 133). No other logic changes — the existing "don't overwrite the
  catalog TCGPlayer price" loop already does the right thing.
- **`fetch_card_prices_all_sources`** (line 77) and **`fetch_card_price`**
  (line 179): add `rarity` param, pass through. Keeps the thin wrappers honest.
- **`update_all_prices`** (line 203): the card snapshot (line 223-235) must
  carry rarity — add `"rarity": c.rarity`. `_fetch_one_card` (line 258) then
  passes `rarity=snap["rarity"]` into the detailed fetch. `models.Card.rarity`
  already exists (`models.py:12`), so no schema change.

Sealed snapshots/queries do **not** get rarity — sealed products have no single
rarity.

## 3. eBay rarity query + fallback (`providers/ebay.py`)

`_search_items` (line 124) builds `q_parts = [query.name, query.set_name]` and
appends `"foil"` / `product_type`. Add rarity, then fall back when too sparse:

- Append `query.rarity` to `q_parts` when present (cards only — sealed never
  sets it).
- After the search, if rarity was included **and** the result count is below a
  threshold, run the search **once more** without rarity and return that
  broader result set.

Because both `fetch` (median) and `fetch_listings` (the on-demand listings
button) call `_search_items`, both inherit the rarity refinement and the
fallback. This is intentional and strictly better — a rarity-scoped listings
button is more accurate, and the fallback protects the sparse case.

The threshold itself is a heuristic that depends on how eBay sellers tag
rarities (often inconsistently). It is the one genuine judgment call in this
chunk and will be decided at implementation time (see "Open heuristic" below).
A starting point is a module constant `_RARITY_MIN_RESULTS` — note the quartile
tier path needs ≥4 prices to trim outliers, so the threshold should sit at or
above that.

## 4. eBay on sealed (`_resolve_sealed_prices_detailed`, `price_service.py:300`)

Today the function returns early: it sets the catalog TCGPlayer price, then only
runs the live path `if not prices:` (line 317). Rewrite it to mirror the **card**
resolver (`fetch_card_prices_all_sources_detailed`):

1. Set the catalog TCGPlayer price (unchanged, line 308-316).
2. Build a sealed `PriceQuery` and loop `get_enabled_providers()` directly,
   adding each provider's price **unless that source is already present** (so a
   live TCGPlayer never clobbers the exact catalog price). Carry tiers when the
   provider exposes them.
3. Only when *nothing* was found, fall back to `_mock_sealed_prices(...)`.

**Why not just call `fetch_sealed_prices_all_sources_detailed` on top?** Because
that path runs through `_aggregate`, which returns **mock prices** when no
provider yields data (`price_service.py:74`). Layering that onto a real catalog
price would inject fake numbers. Looping providers directly — exactly as the
card resolver does — keeps mock strictly as the empty-everything last resort.

Result: sealed with a catalog ID and live eBay data ends up with
`{TCGPlayer: <catalog>, eBay: <median>}`; sealed with a catalog ID but no eBay
hit keeps just `{TCGPlayer}` (no mock pollution).

## 5. Both figures in the UI (frontend)

`price_sources` already carries `{TCGPlayer, eBay}` and `current_price` is the
blend. On the card tile (`TileCard`) and the card detail view, show:

- **TCGPlayer** — `price_sources.TCGPlayer` (per-rarity, the trustworthy
  number).
- **Blended** — `current_price` (the TCG+eBay average, today's headline).

When only one source is present, show just that one (no "Blended" label when
there's nothing to blend). This is a frontend-only change and gets a UI mockup
before implementation per the workspace front-end workflow.

---

## Open heuristic (decide at implementation)

The eBay rarity **fallback threshold** (§3) is the one real design fork: too low
and a single mis-tagged listing sets the price; too high and the fallback fires
constantly, defeating the rarity refinement. This is collector-domain knowledge
(how reliably eBay sellers tag "Starlight Rare" / "Ultra Rare" / "1st Edition"),
so it's the right place for a user-authored heuristic during TDD rather than a
guessed constant.

## Test plan (pytest, AAA, system Python 3.11)

**`PriceQuery`**
- Construct without `rarity` → still valid; `rarity is None`.
- Construct with `rarity="Ultra Rare"` → stored.

**eBay `_search_items` (offline — stub the HTTP/`request_with_backoff`)**
- rarity set → rarity appears in the `q` param.
- rarity unset → `q` unchanged from today (back-compat).
- rarity set but result count < threshold → a second search fires *without*
  rarity; the broader results are returned.
- rarity set and result count ≥ threshold → only one search; no fallback.

**`fetch_card_prices_all_sources_detailed`**
- given a `rarity`, the `PriceQuery` handed to the provider carries it (capture
  via a spy provider).

**`_resolve_sealed_prices_detailed`**
- catalog price present + eBay returns a price → result has BOTH
  `{TCGPlayer, eBay}` (the bug fix).
- catalog price present + eBay returns nothing → result is `{TCGPlayer}` only
  (no mock pollution).
- no catalog identity + eBay present → `{eBay}`.
- nothing anywhere → mock fallback (unchanged behavior).

**Frontend** — component/manual: tile + detail render both figures when
`price_sources` has TCGPlayer + eBay; render one when only one source.

## Out of scope

- DRY-ing the shared "catalog price + live providers on top + mock if empty"
  logic between the card and sealed resolvers into one helper. Tempting, but a
  bigger refactor with more blast radius — note it, don't do it here.
- Changing the blend formula itself. `current_price` stays the simple mean of
  `price_sources`; the UI showing the pure TCGPlayer figure alongside is what
  protects the user from a bad blend, per the approved design.
- Chunk 2 (the "add another rarity" variant button).
- The eBay listings button on sealed (already works; separate on-demand
  endpoint).

## Build order

1. Backend, TDD, in this order (riskiest logic first):
   `PriceQuery.rarity` → eBay `_search_items` rarity + fallback → card-path
   threading → sealed resolver rewrite. All offline; system Python 3.11.
2. Frontend both-figures (mockup first, then `TileCard` + detail).
3. Deploy to Pi + live verify (user-gated): confirm Ultra vs Starlight now
   diverge and sealed shows eBay.
