"""Price aggregation across configured providers, with deterministic mock fallback."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, Optional, Tuple

from database import SessionLocal
from providers import PriceQuery, catalog, get_enabled_providers
import models

logger = logging.getLogger(__name__)

# Phase E: how many cards' external API calls run in parallel during the
# scheduler's batch refresh. 4 is a polite default — fits inside typical
# rate limits even for 100+ card collections at 4×/day cadence. Override
# via env for stress testing or larger collections.
_MAX_REFRESH_WORKERS = int(os.environ.get("PRICE_REFRESH_WORKERS", "4"))

# Deterministic mock prices used in dev/test when no provider credentials are present.
# Real prices are noisy; mocks are stable so tests/snapshots are reproducible.
_MOCK_BASE_CARD = {"magic": 10.0, "pokemon": 15.0, "yugioh": 8.0}
_MOCK_BASE_SEALED = {
    "magic": {"booster box": 120.0, "pack": 4.0, "deck": 25.0},
    "pokemon": {"booster box": 150.0, "pack": 5.0, "deck": 30.0},
    "yugioh": {"booster box": 100.0, "pack": 3.5, "deck": 20.0},
}
_MOCK_SOURCE_OFFSETS = {"TCGPlayer": 1.00, "eBay": 0.92, "CardMarket": 1.08}


def _mock_card_prices(name: str, set_name: str, game: str, is_foil: bool) -> Dict[str, float]:
    base = _MOCK_BASE_CARD.get(game.lower(), 5.0)
    multiplier = 2.0 if is_foil else 1.0
    set_modifier = (hash(set_name) % 100) / 100.0
    anchor = base * multiplier * (1 + set_modifier)
    return {src: round(anchor * mult, 2) for src, mult in _MOCK_SOURCE_OFFSETS.items()}


def _mock_sealed_prices(
    name: str, set_name: str, product_type: str, game: str
) -> Dict[str, float]:
    table = _MOCK_BASE_SEALED.get(game.lower(), {})
    base = table.get(product_type.lower(), 10.0)
    set_modifier = (hash(set_name) % 50) / 100.0
    anchor = base * (1 + set_modifier)
    return {src: round(anchor * mult, 2) for src, mult in _MOCK_SOURCE_OFFSETS.items()}


def _aggregate(query: PriceQuery, mock_fn) -> Tuple[Dict[str, float], Dict[str, dict]]:
    """Run all configured providers; fall back to mocks if no live data.

    Returns ``(prices_by_source, tiers_by_source)``. ``tiers_by_source`` only
    carries sources that expose per-tier prices (TCGPlayer low/mid/high/market);
    it is empty for sources that return a single aggregate price and on mock
    fallback.
    """
    providers = get_enabled_providers()
    out: Dict[str, float] = {}
    tiers: Dict[str, dict] = {}
    for provider in providers:
        try:
            result = provider.fetch(query)
        except Exception as exc:
            logger.exception("Provider %s raised: %s", provider.name, exc)
            continue
        if result and result.price is not None:
            out[result.source] = round(float(result.price), 2)
            if getattr(result, "tiers", None):
                tiers[result.source] = result.tiers
    if out:
        return out, tiers
    return mock_fn(), {}


def headline_price(prices: Dict[str, float]) -> Optional[float]:
    """The single ``current_price`` shown for an item.

    TCGPlayer is the exact per-printing market price, so when present it is the
    headline on its own. eBay runs systematically high (shipping-price floors on
    cheap cards, plus graded/lot listings) so blending it in inflated the
    headline — instead it stays in ``price_sources`` as a secondary signal. With
    no TCGPlayer price, fall back to the mean of whatever sources exist.
    """
    if not prices:
        return None
    if "TCGPlayer" in prices:
        return prices["TCGPlayer"]
    return round(sum(prices.values()) / len(prices), 2)


def fetch_card_prices_all_sources(
    name: str,
    set_name: str,
    game: str,
    is_foil: bool = False,
    external_source: Optional[str] = None,
    external_id: Optional[str] = None,
    tcgplayer_product_id: Optional[str] = None,
    rarity: Optional[str] = None,
) -> Dict[str, float]:
    """Aggregate prices for a card (source -> price). See the detailed variant
    for the full contract; this thin wrapper drops the tier data for callers
    that only need the per-source prices."""
    prices, _ = fetch_card_prices_all_sources_detailed(
        name, set_name, game, is_foil,
        external_source=external_source, external_id=external_id,
        tcgplayer_product_id=tcgplayer_product_id, rarity=rarity,
    )
    return prices


def fetch_card_prices_all_sources_detailed(
    name: str,
    set_name: str,
    game: str,
    is_foil: bool = False,
    external_source: Optional[str] = None,
    external_id: Optional[str] = None,
    tcgplayer_product_id: Optional[str] = None,
    rarity: Optional[str] = None,
) -> Tuple[Dict[str, float], Dict[str, dict]]:
    """Aggregate prices for a card, returning ``(prices, tiers_by_source)``.

    When the card was linked to a public catalog (Scryfall / PokemonTCG.io / YGOPRODeck)
    we trust that source for the TCGPlayer price — it is keyed by an exact catalog ID
    instead of fuzzy name matching. Other configured providers still run on top.

    ``tcgplayer_product_id`` is preferred over the per-game catalog because TCGplayer's
    ``marketPrice`` is per-printing, while per-game catalogs sometimes carry zero or
    aggregate-only prices (Yu-Gi-Oh! Starlight Rare being the canonical example).

    ``tiers_by_source`` carries low/mid/high/market only for sources that expose
    them (the live TCGPlayer provider). The catalog-derived TCGPlayer price is a
    single value with no tiers.
    """
    out: Dict[str, float] = {}
    tiers: Dict[str, dict] = {}
    if (external_source and external_id) or tcgplayer_product_id:
        catalog_price = catalog.fetch_tcgplayer_price(
            external_source or "",
            external_id or "",
            is_foil,
            set_name=set_name,
            tcgplayer_product_id=tcgplayer_product_id,
        )
        if catalog_price is not None:
            out["TCGPlayer"] = round(float(catalog_price), 2)

    query = PriceQuery(name=name, set_name=set_name, game=game, is_foil=is_foil, rarity=rarity)
    providers = get_enabled_providers()
    for provider in providers:
        # Don't overwrite the catalog-derived TCGPlayer price.
        if provider.name in out:
            continue
        try:
            result = provider.fetch(query)
        except Exception as exc:
            logger.exception("Provider %s raised: %s", provider.name, exc)
            continue
        if result and result.price is not None:
            out[result.source] = round(float(result.price), 2)
            if getattr(result, "tiers", None):
                tiers[result.source] = result.tiers

    if out:
        return out, tiers
    return _mock_card_prices(name, set_name, game, is_foil), {}


def fetch_sealed_prices_all_sources(
    name: str, set_name: str, product_type: str, game: str
) -> Dict[str, float]:
    prices, _ = fetch_sealed_prices_all_sources_detailed(
        name, set_name, product_type, game
    )
    return prices


def fetch_sealed_prices_all_sources_detailed(
    name: str, set_name: str, product_type: str, game: str
) -> Tuple[Dict[str, float], Dict[str, dict]]:
    """Aggregate sealed prices, returning ``(prices, tiers_by_source)``."""
    query = PriceQuery(
        name=name,
        set_name=set_name,
        game=game,
        is_sealed=True,
        product_type=product_type,
    )
    return _aggregate(
        query, lambda: _mock_sealed_prices(name, set_name, product_type, game)
    )


def fetch_card_price(
    name: str,
    set_name: str,
    game: str,
    is_foil: bool = False,
    external_source: Optional[str] = None,
    external_id: Optional[str] = None,
    tcgplayer_product_id: Optional[str] = None,
    rarity: Optional[str] = None,
) -> Optional[float]:
    prices = fetch_card_prices_all_sources(
        name, set_name, game, is_foil,
        external_source=external_source, external_id=external_id,
        tcgplayer_product_id=tcgplayer_product_id, rarity=rarity,
    )
    return headline_price(prices)


def fetch_sealed_price(
    name: str, set_name: str, product_type: str, game: str
) -> Optional[float]:
    prices = fetch_sealed_prices_all_sources(name, set_name, product_type, game)
    return headline_price(prices)


def update_all_prices() -> None:
    """Refresh per-source prices for every card/sealed and log to PriceHistory.

    Implementation note (Phase B fix): the previous version held the
    ``SessionLocal()`` open across every external HTTP call to all providers,
    which serialized concurrent ``GET /cards/`` requests behind the refresh
    on SQLite (single-writer). The fix is a snapshot-then-write pattern:

    1. Open a short session, snapshot the IDs + identity fields needed to
       fetch prices, close it. No HTTP done yet.
    2. Make the external HTTP calls with no session open.
    3. Open a fresh short session per row to write the result.

    This means a /cards/ request can land between cards rather than waiting
    for the entire batch to finish.
    """
    from crud import log_price_history

    # ---- Phase 1: snapshot identity fields (no external I/O yet) ---------
    with SessionLocal() as db:
        card_snaps = [
            {
                "id": c.id,
                "name": c.name,
                "set_name": c.set_name,
                "game": c.game,
                "is_foil": c.is_foil,
                "rarity": c.rarity,
                "external_source": c.external_source,
                "external_id": c.external_id,
                "tcgplayer_product_id": c.tcgplayer_product_id,
            }
            for c in db.query(models.Card).all()
        ]
        sealed_snaps = [
            {
                "id": s.id,
                "name": s.name,
                "set_name": s.set_name,
                "product_type": s.product_type,
                "game": s.game,
                "external_source": s.external_source,
                "external_id": s.external_id,
                "tcgplayer_product_id": s.tcgplayer_product_id,
            }
            for s in db.query(models.SealedProduct).all()
        ]

    now = datetime.utcnow()

    # ---- Phase 2: parallel external fetch (no DB session held) -----------
    # ThreadPoolExecutor fans out the per-card provider calls. External APIs
    # are I/O-bound, so threads (not processes) are the right tool — Python's
    # GIL is irrelevant when threads are blocked on socket reads. Drops the
    # ~5-8min serial refresh on 100 cards to ~1-2min, which is the prerequisite
    # for sub-day cadence (4×/day).
    def _fetch_one_card(snap: Dict) -> Optional[tuple]:
        try:
            prices, tiers = fetch_card_prices_all_sources_detailed(
                snap["name"], snap["set_name"], snap["game"], snap["is_foil"],
                external_source=snap["external_source"],
                external_id=snap["external_id"],
                tcgplayer_product_id=snap["tcgplayer_product_id"],
                rarity=snap["rarity"],
            )
            return (snap["id"], prices, tiers)
        except Exception as exc:  # noqa: BLE001 — one bad card shouldn't kill the batch
            logger.exception("Price fetch failed for card %s: %s", snap["id"], exc)
            return None

    def _fetch_one_sealed(snap: Dict) -> Optional[tuple]:
        try:
            prices, tiers = _resolve_sealed_prices_detailed(snap)
            return (snap["id"], prices, tiers)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Price fetch failed for sealed %s: %s", snap["id"], exc)
            return None

    # ---- Phase 3: persist results sequentially (one short tx per row) ----
    # Concurrency is in the EXTERNAL fetch only. DB writes stay sequential
    # because SQLite is single-writer; parallelizing them would just thrash
    # on the write lock.
    if card_snaps:
        with ThreadPoolExecutor(max_workers=_MAX_REFRESH_WORKERS) as pool:
            for result in pool.map(_fetch_one_card, card_snaps):
                if result is None:
                    continue
                card_id, prices, tiers = result
                _persist_card_prices(card_id, prices, tiers, now, log_price_history)

    if sealed_snaps:
        with ThreadPoolExecutor(max_workers=_MAX_REFRESH_WORKERS) as pool:
            for result in pool.map(_fetch_one_sealed, sealed_snaps):
                if result is None:
                    continue
                sealed_id, prices, tiers = result
                _persist_sealed_prices(sealed_id, prices, tiers, now, log_price_history)


def _resolve_sealed_prices_detailed(snap: Dict) -> Tuple[Dict[str, float], Dict[str, dict]]:
    """Per-row sealed price resolution. Pure function — no DB.

    Returns ``(prices, tiers_by_source)``. The catalog-derived TCGPlayer price
    carries no tiers; tiers only come from the live aggregate path.
    """
    prices: Dict[str, float] = {}
    tiers: Dict[str, dict] = {}
    if (snap["external_source"] and snap["external_id"]) or snap["tcgplayer_product_id"]:
        catalog_price = catalog.fetch_tcgplayer_price(
            snap["external_source"] or "", snap["external_id"] or "",
            is_foil=False,
            set_name=snap["set_name"],
            tcgplayer_product_id=snap["tcgplayer_product_id"],
        )
        if catalog_price is not None:
            prices["TCGPlayer"] = round(float(catalog_price), 2)

    # Run live providers (eBay) ON TOP of the catalog price — mirrors the card
    # resolver. Previously this path was guarded by ``if not prices`` and so
    # never fired once the catalog TCGPlayer price landed, which is why sealed
    # products carried no eBay data. A live source never overwrites the exact
    # per-product catalog price.
    query = PriceQuery(
        name=snap["name"], set_name=snap["set_name"], game=snap["game"],
        is_sealed=True, product_type=snap["product_type"],
    )
    for provider in get_enabled_providers():
        if provider.name in prices:
            continue
        try:
            result = provider.fetch(query)
        except Exception as exc:  # noqa: BLE001 — one bad provider shouldn't kill the row
            logger.exception("Provider %s raised on sealed %s: %s",
                             provider.name, snap.get("id"), exc)
            continue
        if result and result.price is not None:
            prices[result.source] = round(float(result.price), 2)
            if getattr(result, "tiers", None):
                tiers[result.source] = result.tiers

    # Mock only as the true last resort — when neither catalog nor any live
    # provider produced a price. Never merged on top of real data.
    if not prices:
        prices = _mock_sealed_prices(
            snap["name"], snap["set_name"], snap["product_type"], snap["game"]
        )
    return prices, tiers


def _persist_card_prices(card_id: int, prices: Dict[str, float], tiers_by_source: Dict[str, dict], now: datetime, log_price_history) -> None:
    """Open a short transaction, write the result, close. Yields between cards."""
    with SessionLocal() as db:
        card = db.query(models.Card).filter(models.Card.id == card_id).first()
        if card is None:
            logger.warning("Card %s vanished mid-refresh, skipping persist", card_id)
            return
        for source, price in prices.items():
            try:
                log_price_history(db, "card", card.id, source, price, ts=now,
                                  tiers=tiers_by_source.get(source))
            except Exception as exc:  # noqa: BLE001
                logger.warning("PriceHistory write failed (card %s): %s", card.id, exc)
        card.current_price = headline_price(prices)
        card.price_sources = prices or None
        card.last_price_update = now
        db.commit()


def _persist_sealed_prices(sealed_id: int, prices: Dict[str, float], tiers_by_source: Dict[str, dict], now: datetime, log_price_history) -> None:
    with SessionLocal() as db:
        sealed = db.query(models.SealedProduct).filter(
            models.SealedProduct.id == sealed_id
        ).first()
        if sealed is None:
            logger.warning("Sealed %s vanished mid-refresh, skipping persist", sealed_id)
            return
        for source, price in prices.items():
            try:
                log_price_history(db, "sealed", sealed.id, source, price, ts=now,
                                  tiers=tiers_by_source.get(source))
            except Exception as exc:  # noqa: BLE001
                logger.warning("PriceHistory write failed (sealed %s): %s", sealed.id, exc)
        sealed.current_price = headline_price(prices)
        sealed.price_sources = prices or None
        sealed.last_price_update = now
        db.commit()
