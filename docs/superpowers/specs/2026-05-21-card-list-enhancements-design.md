# Design: Card List + Add-Flow Enhancements

**Date:** 2026-05-21
**Project:** card-collection-anime
**Status:** approved (design forks confirmed with user)

## Summary

Five focused enhancements to the card list + add flow:
1. Duplicate-on-add → auto-increment quantity (+1) instead of a new entry, with a toast.
2. "Name + rarity" sort, made the default (A→Z, rarest-first within same name).
3. Rarity badge on the card tile, clickable to filter by that rarity.
4. Sticky top search bar (reachable while scrolling); `/` focuses it.
5. (supporting) `merged` flag plumbed through the create response.

No schema columns needed — every field (`rarity`, `condition`, `is_foil`,
`external_id`, `tcgplayer_product_id`, `quantity`) already exists.

## Confirmed decisions

- **Dedup key:** exact printing + foil + condition.
- **Grouping:** keep per-game sections; sort applies within each.
- **Rarity badge:** clickable → sets the rarity filter.
- **Same-name + same-rarity tiebreak:** set name A→Z (default; user may revise).

---

## 1. Duplicate-on-add (backend)

`crud.create_card`: before inserting, search for an existing card with the same
**identity**, where identity is the first available of:
- `tcgplayer_product_id` (exact), else
- `external_source` + `external_id` (exact), else
- `name` + `set_name` + `rarity` (case-insensitive) — manual/keyless fallback

…**AND** matching `is_foil` **AND** matching `condition`.

- **Match:** increment existing `quantity` by the incoming `quantity` (default 1),
  commit, set transient `db_card.merged = True`, return it.
- **No match:** insert as today; set `db_card.merged = False`.

`schemas.Card` gains `merged: Optional[bool] = None` (read from the transient
attr via `from_attributes`). Existing callers/tests that read `id`/`name`/price
are unaffected — `merged` is additive.

Identity match runs against all cards (small collection; SQLite). The price
fetch on a *merge* is skipped — we only bump quantity, keeping the existing
`current_price`/`price_sources`/`acquired_price` intact.

### Tests
- Re-adding same product_id+foil+condition → qty increments, one row, `merged=True`.
- Same name, different rarity → two separate rows (no merge).
- Same printing, different foil → separate. Different condition → separate.
- Manual (no IDs) same name+set+rarity+foil+condition → merges.
- First add → `merged=False`.

## 2. Merge toast (frontend `AddCardPage`)

After `createCard`, branch on `res.data.merged`:
- `true` → `Already had "{name}" — added +1 (now ×{quantity})`
- `false` → existing "Saved" behavior

Applies to both "Save & Done" (brief message before redirect) and "Save & Add
Another" (reuses the existing `savedToast`).

## 3. "Name + rarity" sort, default (`CardListPage`)

New helper `rarityRank(rarity, game)`: case-insensitive index of `rarity` in
`RARITIES_BY_GAME[game]` (already ordered common→rarest, so higher index =
rarer); returns -1 for unknown/free-typed.

New `sortComparator` case `name-rarity`:
1. `name` `localeCompare` (A→Z)
2. tie → `rarityRank` **descending** (rarest first); unknown (-1) sorts last
3. tie → `set_name` `localeCompare`

`SORT_LABELS['name-rarity'] = 'A → Z (rarity)'`. `DEFAULT_FILTERS.sort =
'name-rarity'`. Bump `FILTER_STORAGE_KEY` `v1`→`v2` so existing saved `newest`
resets to the new default. Sort runs before `groupByGame`, which preserves input
order within each section → alphabetical+rarity within each game group.

## 4. Rarity badge on tile (`TileCard`)

Corner chip rendering `item.rarity` when present, placed **outside** the detail
`<Link>` (like the qty pill) so a click doesn't navigate. New optional prop
`onRarityClick(rarity)`:
- provided (cards list) → badge is a `<button>`; click calls it (CardListPage
  sets `filters.rarity`).
- absent (sealed list) → not applicable; sealed have no rarity, badge hidden.

`CardListPage` passes `onRarityClick={(r) => setFilters((f) => ({ ...f, rarity: r }))}`.

## 5. Sticky search (`CardListPage` + `App.css`)

The first `.filter-row` (search + sort + add buttons) gets
`position: sticky; top: 0` + background + `z-index` so it stays visible while
the grid scrolls. Bonus: a `keydown` listener focuses the search input on `/`
(ignored when already typing in a field).

---

## Out of scope
- DB schema changes (none needed).
- Sealed list changes beyond TileCard's hidden-when-absent badge.
- Bulk re-dedup of existing duplicate rows (this only affects new adds).

## Build order
1. Backend dedup + `merged` flag (+ pytest) — the riskiest, do first.
2. Frontend: rarityRank + sort default; TileCard badge; CardListPage wiring + sticky; AddCardPage toast.
3. Local build + (when eBay/Pi available) live verify.
