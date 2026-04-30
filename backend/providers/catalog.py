"""Public catalog search across the three games.

Each game uses a free, no-auth public API:
- Magic    -> Scryfall            https://api.scryfall.com
- Pokemon  -> PokemonTCG.io       https://api.pokemontcg.io
- Yu-Gi-Oh -> YGOPRODeck          https://db.ygoprodeck.com

All return TCGplayer-derived prices alongside catalog metadata, so we can both
search a real product and pull an authoritative TCGplayer price in one round trip.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .base import request_with_backoff

logger = logging.getLogger(__name__)


def search(query: str, game: str, limit: int = 12) -> List[Dict]:
    """Dispatch to the per-game catalog. Returns normalized CatalogResult dicts."""
    g = (game or "").lower()
    if g == "magic":
        return _search_scryfall(query, limit)
    if g == "pokemon":
        return _search_pokemontcg(query, limit)
    if g == "yugioh":
        return _search_ygoprodeck(query, limit)
    return []


def fetch_tcgplayer_price(
    external_source: str, external_id: str, is_foil: bool = False
) -> Optional[float]:
    """Refresh the authoritative TCGplayer price for a previously linked card."""
    try:
        if external_source == "scryfall":
            return _scryfall_price(external_id, is_foil)
        if external_source == "pokemontcg":
            return _pokemontcg_price(external_id, is_foil)
        if external_source == "ygoprodeck":
            return _ygoprodeck_price(external_id)
    except Exception as exc:
        logger.warning("Catalog price refresh failed (%s/%s): %s", external_source, external_id, exc)
    return None


# ---------------------------------------------------------------------------
# Scryfall (Magic: The Gathering)
# ---------------------------------------------------------------------------

def _search_scryfall(query: str, limit: int) -> List[Dict]:
    resp = request_with_backoff(
        "GET",
        "https://api.scryfall.com/cards/search",
        params={"q": query, "unique": "prints", "order": "released"},
        headers={"User-Agent": "card-collection-anime/1.0", "Accept": "application/json"},
    )
    if not resp or resp.status_code >= 400:
        return []
    data = resp.json()
    out: List[Dict] = []
    for card in (data.get("data") or [])[:limit]:
        prices = card.get("prices") or {}
        usd = _safe_float(prices.get("usd"))
        usd_foil = _safe_float(prices.get("usd_foil"))
        image = (card.get("image_uris") or {}).get("small") or (
            (card.get("card_faces") or [{}])[0].get("image_uris") or {}
        ).get("small")
        out.append({
            "external_source": "scryfall",
            "external_id": card.get("id", ""),
            "name": card.get("name", ""),
            "set_name": card.get("set_name", ""),
            "image_url": image,
            "tcgplayer_price": usd,
            "tcgplayer_price_foil": usd_foil,
            "rarity": card.get("rarity"),
        })
    return out


def _scryfall_price(card_id: str, is_foil: bool) -> Optional[float]:
    resp = request_with_backoff(
        "GET",
        f"https://api.scryfall.com/cards/{card_id}",
        headers={"User-Agent": "card-collection-anime/1.0", "Accept": "application/json"},
    )
    if not resp or resp.status_code >= 400:
        return None
    prices = (resp.json() or {}).get("prices") or {}
    return _safe_float(prices.get("usd_foil") if is_foil else prices.get("usd"))


# ---------------------------------------------------------------------------
# PokemonTCG.io (Pokemon)
# ---------------------------------------------------------------------------

def _search_pokemontcg(query: str, limit: int) -> List[Dict]:
    resp = request_with_backoff(
        "GET",
        "https://api.pokemontcg.io/v2/cards",
        params={"q": f'name:"{query}*"', "pageSize": limit, "orderBy": "-set.releaseDate"},
        headers={"Accept": "application/json"},
    )
    if not resp or resp.status_code >= 400:
        return []
    out: List[Dict] = []
    for card in (resp.json().get("data") or [])[:limit]:
        tcg = ((card.get("tcgplayer") or {}).get("prices") or {})
        # PokemonTCG.io reports prices per variant (normal, holofoil, reverseHolofoil, ...).
        # Pick the most likely "default" tier; foil takes precedence for holo variants.
        normal = _safe_float((tcg.get("normal") or {}).get("market"))
        holo = _safe_float((tcg.get("holofoil") or {}).get("market"))
        out.append({
            "external_source": "pokemontcg",
            "external_id": card.get("id", ""),
            "name": card.get("name", ""),
            "set_name": (card.get("set") or {}).get("name", ""),
            "image_url": (card.get("images") or {}).get("small"),
            "tcgplayer_price": normal or holo,
            "tcgplayer_price_foil": holo,
            "rarity": card.get("rarity"),
        })
    return out


def _pokemontcg_price(card_id: str, is_foil: bool) -> Optional[float]:
    resp = request_with_backoff(
        "GET",
        f"https://api.pokemontcg.io/v2/cards/{card_id}",
        headers={"Accept": "application/json"},
    )
    if not resp or resp.status_code >= 400:
        return None
    tcg = (((resp.json() or {}).get("data") or {}).get("tcgplayer") or {}).get("prices") or {}
    if is_foil:
        return _safe_float((tcg.get("holofoil") or {}).get("market")) \
            or _safe_float((tcg.get("reverseHolofoil") or {}).get("market"))
    return _safe_float((tcg.get("normal") or {}).get("market")) \
        or _safe_float((tcg.get("holofoil") or {}).get("market"))


# ---------------------------------------------------------------------------
# YGOPRODeck (Yu-Gi-Oh!)
# ---------------------------------------------------------------------------

def _search_ygoprodeck(query: str, limit: int) -> List[Dict]:
    resp = request_with_backoff(
        "GET",
        "https://db.ygoprodeck.com/api/v7/cardinfo.php",
        params={"fname": query, "num": limit, "offset": 0},
        headers={"Accept": "application/json"},
    )
    if not resp or resp.status_code >= 400:
        return []
    out: List[Dict] = []
    for card in (resp.json().get("data") or [])[:limit]:
        prices = (card.get("card_prices") or [{}])[0]
        sets = card.get("card_sets") or []
        set_name = sets[0].get("set_name") if sets else ""
        image = ((card.get("card_images") or [{}])[0]).get("image_url_small")
        # YGOPRODeck IDs are integers; coerce to str so we have a uniform key.
        out.append({
            "external_source": "ygoprodeck",
            "external_id": str(card.get("id", "")),
            "name": card.get("name", ""),
            "set_name": set_name,
            "image_url": image,
            "tcgplayer_price": _safe_float(prices.get("tcgplayer_price")),
            "tcgplayer_price_foil": None,  # YGOPRODeck doesn't break out foil
            "rarity": card.get("type"),
        })
    return out


def _ygoprodeck_price(card_id: str) -> Optional[float]:
    resp = request_with_backoff(
        "GET",
        "https://db.ygoprodeck.com/api/v7/cardinfo.php",
        params={"id": card_id},
        headers={"Accept": "application/json"},
    )
    if not resp or resp.status_code >= 400:
        return None
    items = (resp.json() or {}).get("data") or []
    if not items:
        return None
    prices = (items[0].get("card_prices") or [{}])[0]
    return _safe_float(prices.get("tcgplayer_price"))


# ---------------------------------------------------------------------------

def _safe_float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None
