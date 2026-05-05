"""Price aggregation across configured providers, with deterministic mock fallback."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional

from database import SessionLocal
from providers import PriceQuery, catalog, get_enabled_providers
import models

logger = logging.getLogger(__name__)

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


def _aggregate(query: PriceQuery, mock_fn) -> Dict[str, float]:
    """Run all configured providers; fall back to mocks if no live data."""
    providers = get_enabled_providers()
    out: Dict[str, float] = {}
    for provider in providers:
        try:
            result = provider.fetch(query)
        except Exception as exc:
            logger.exception("Provider %s raised: %s", provider.name, exc)
            continue
        if result and result.price is not None:
            out[result.source] = round(float(result.price), 2)
    if out:
        return out
    return mock_fn()


def fetch_card_prices_all_sources(
    name: str,
    set_name: str,
    game: str,
    is_foil: bool = False,
    external_source: Optional[str] = None,
    external_id: Optional[str] = None,
    tcgplayer_product_id: Optional[str] = None,
) -> Dict[str, float]:
    """Aggregate prices for a card.

    When the card was linked to a public catalog (Scryfall / PokemonTCG.io / YGOPRODeck)
    we trust that source for the TCGPlayer price — it is keyed by an exact catalog ID
    instead of fuzzy name matching. Other configured providers still run on top.

    ``tcgplayer_product_id`` is preferred over the per-game catalog because TCGplayer's
    ``marketPrice`` is per-printing, while per-game catalogs sometimes carry zero or
    aggregate-only prices (Yu-Gi-Oh! Starlight Rare being the canonical example).
    """
    out: Dict[str, float] = {}
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

    query = PriceQuery(name=name, set_name=set_name, game=game, is_foil=is_foil)
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

    if out:
        return out
    return _mock_card_prices(name, set_name, game, is_foil)


def fetch_sealed_prices_all_sources(
    name: str, set_name: str, product_type: str, game: str
) -> Dict[str, float]:
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
) -> Optional[float]:
    prices = fetch_card_prices_all_sources(
        name, set_name, game, is_foil,
        external_source=external_source, external_id=external_id,
        tcgplayer_product_id=tcgplayer_product_id,
    )
    return round(sum(prices.values()) / len(prices), 2) if prices else None


def fetch_sealed_price(
    name: str, set_name: str, product_type: str, game: str
) -> Optional[float]:
    prices = fetch_sealed_prices_all_sources(name, set_name, product_type, game)
    return round(sum(prices.values()) / len(prices), 2) if prices else None


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

    # ---- Phase 2: refresh each card with no DB session held --------------
    for snap in card_snaps:
        try:
            prices = fetch_card_prices_all_sources(
                snap["name"], snap["set_name"], snap["game"], snap["is_foil"],
                external_source=snap["external_source"],
                external_id=snap["external_id"],
                tcgplayer_product_id=snap["tcgplayer_product_id"],
            )
        except Exception as exc:  # noqa: BLE001 — one bad card shouldn't kill the batch
            logger.exception("Price fetch failed for card %s: %s", snap["id"], exc)
            continue
        _persist_card_prices(snap["id"], prices, now, log_price_history)

    for snap in sealed_snaps:
        try:
            prices = _resolve_sealed_prices(snap)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Price fetch failed for sealed %s: %s", snap["id"], exc)
            continue
        _persist_sealed_prices(snap["id"], prices, now, log_price_history)


def _resolve_sealed_prices(snap: Dict) -> Dict[str, float]:
    """Per-row sealed price resolution. Pure function — no DB."""
    prices: Dict[str, float] = {}
    if (snap["external_source"] and snap["external_id"]) or snap["tcgplayer_product_id"]:
        catalog_price = catalog.fetch_tcgplayer_price(
            snap["external_source"] or "", snap["external_id"] or "",
            is_foil=False,
            set_name=snap["set_name"],
            tcgplayer_product_id=snap["tcgplayer_product_id"],
        )
        if catalog_price is not None:
            prices["TCGPlayer"] = round(float(catalog_price), 2)
    if not prices:
        prices = fetch_sealed_prices_all_sources(
            snap["name"], snap["set_name"], snap["product_type"], snap["game"]
        )
    return prices


def _persist_card_prices(card_id: int, prices: Dict[str, float], now: datetime, log_price_history) -> None:
    """Open a short transaction, write the result, close. Yields between cards."""
    with SessionLocal() as db:
        card = db.query(models.Card).filter(models.Card.id == card_id).first()
        if card is None:
            logger.warning("Card %s vanished mid-refresh, skipping persist", card_id)
            return
        for source, price in prices.items():
            try:
                log_price_history(db, "card", card.id, source, price, ts=now)
            except Exception as exc:  # noqa: BLE001
                logger.warning("PriceHistory write failed (card %s): %s", card.id, exc)
        card.current_price = (
            round(sum(prices.values()) / len(prices), 2) if prices else None
        )
        card.price_sources = prices or None
        card.last_price_update = now
        db.commit()


def _persist_sealed_prices(sealed_id: int, prices: Dict[str, float], now: datetime, log_price_history) -> None:
    with SessionLocal() as db:
        sealed = db.query(models.SealedProduct).filter(
            models.SealedProduct.id == sealed_id
        ).first()
        if sealed is None:
            logger.warning("Sealed %s vanished mid-refresh, skipping persist", sealed_id)
            return
        for source, price in prices.items():
            try:
                log_price_history(db, "sealed", sealed.id, source, price, ts=now)
            except Exception as exc:  # noqa: BLE001
                logger.warning("PriceHistory write failed (sealed %s): %s", sealed.id, exc)
        sealed.current_price = (
            round(sum(prices.values()) / len(prices), 2) if prices else None
        )
        sealed.price_sources = prices or None
        sealed.last_price_update = now
        db.commit()
