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
) -> Dict[str, float]:
    """Aggregate prices for a card.

    When the card was linked to a public catalog (Scryfall / PokemonTCG.io / YGOPRODeck)
    we trust that source for the TCGPlayer price — it is keyed by an exact catalog ID
    instead of fuzzy name matching. Other configured providers still run on top.
    """
    out: Dict[str, float] = {}
    if external_source and external_id:
        catalog_price = catalog.fetch_tcgplayer_price(
            external_source, external_id, is_foil, set_name=set_name
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
) -> Optional[float]:
    prices = fetch_card_prices_all_sources(
        name, set_name, game, is_foil,
        external_source=external_source, external_id=external_id,
    )
    return round(sum(prices.values()) / len(prices), 2) if prices else None


def fetch_sealed_price(
    name: str, set_name: str, product_type: str, game: str
) -> Optional[float]:
    prices = fetch_sealed_prices_all_sources(name, set_name, product_type, game)
    return round(sum(prices.values()) / len(prices), 2) if prices else None


def update_all_prices() -> None:
    """Refresh per-source prices for every card/sealed and log to PriceHistory."""
    from crud import log_price_history

    db = SessionLocal()
    try:
        now = datetime.utcnow()

        for card in db.query(models.Card).all():
            prices = fetch_card_prices_all_sources(
                card.name, card.set_name, card.game, card.is_foil,
                external_source=card.external_source,
                external_id=card.external_id,
            )
            for source, price in prices.items():
                try:
                    log_price_history(db, "card", card.id, source, price, ts=now)
                except Exception as exc:
                    logger.warning(
                        "PriceHistory write failed (card %s): %s", card.id, exc
                    )
            card.current_price = (
                round(sum(prices.values()) / len(prices), 2) if prices else None
            )
            card.price_sources = prices
            card.last_price_update = now

        for sealed in db.query(models.SealedProduct).all():
            prices: Dict[str, float] = {}
            if sealed.external_source and sealed.external_id:
                catalog_price = catalog.fetch_tcgplayer_price(
                    sealed.external_source, sealed.external_id, is_foil=False,
                    set_name=sealed.set_name,
                )
                if catalog_price is not None:
                    prices["TCGPlayer"] = round(float(catalog_price), 2)
            if not prices:
                prices = fetch_sealed_prices_all_sources(
                    sealed.name, sealed.set_name, sealed.product_type, sealed.game
                )
            for source, price in prices.items():
                try:
                    log_price_history(db, "sealed", sealed.id, source, price, ts=now)
                except Exception as exc:
                    logger.warning(
                        "PriceHistory write failed (sealed %s): %s", sealed.id, exc
                    )
            sealed.current_price = (
                round(sum(prices.values()) / len(prices), 2) if prices else None
            )
            sealed.price_sources = prices
            sealed.last_price_update = now

        db.commit()
    finally:
        db.close()
