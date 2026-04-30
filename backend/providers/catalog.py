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
import re
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

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


def resolve_url(url: str) -> Optional[Dict]:
    """Detect what kind of catalog URL we got and resolve it to a CatalogResult.

    Supports:
      * scryfall.com/card/<set>/<collector_number>[/...]      (Magic)
      * scryfall.com/.../<uuid>                               (Magic)
      * tcgplayer.com/product/<productId>/...                 (Magic via Scryfall;
                                                               Pokemon via PokemonTCG.io)
      * db.ygoprodeck.com/card/?search=<id-or-name>           (Yu-Gi-Oh)
      * pokemontcg.io URLs containing a card id segment       (Pokemon)
    """
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    qs = parse_qs(parsed.query or "")

    if "scryfall.com" in host:
        return _resolve_scryfall_url(path)
    if "tcgplayer.com" in host:
        return _resolve_tcgplayer_url(path, url)
    if "ygoprodeck.com" in host:
        return _resolve_ygoprodeck_url(qs)
    if "pokemontcg.io" in host:
        return _resolve_pokemontcg_url(path)
    return None


def _resolve_scryfall_url(path: str) -> Optional[Dict]:
    # /card/<set>/<num>[/<name>] OR /cards/<uuid>
    set_num = re.match(r"^/card/([^/]+)/([^/?#]+)", path)
    if set_num:
        set_code, num = set_num.group(1), set_num.group(2)
        resp = request_with_backoff(
            "GET",
            f"https://api.scryfall.com/cards/{set_code}/{num}",
            headers={"User-Agent": "card-collection-anime/1.0", "Accept": "application/json"},
        )
        return _scryfall_card_to_result(resp.json()) if resp and resp.status_code == 200 else None
    uuid = re.match(r".*/([0-9a-f-]{36})$", path)
    if uuid:
        resp = request_with_backoff(
            "GET",
            f"https://api.scryfall.com/cards/{uuid.group(1)}",
            headers={"User-Agent": "card-collection-anime/1.0", "Accept": "application/json"},
        )
        return _scryfall_card_to_result(resp.json()) if resp and resp.status_code == 200 else None
    return None


def _resolve_tcgplayer_url(path: str, full_url: str) -> Optional[Dict]:
    m = re.match(r"^/product/(\d+)", path)
    if not m:
        return None
    product_id = m.group(1)

    # Magic: Scryfall has /cards/tcgplayer/<id>.
    resp = request_with_backoff(
        "GET",
        f"https://api.scryfall.com/cards/tcgplayer/{product_id}",
        headers={"User-Agent": "card-collection-anime/1.0", "Accept": "application/json"},
    )
    if resp and resp.status_code == 200:
        return _scryfall_card_to_result(resp.json())

    # Pokemon: PokemonTCG.io supports filtering by tcgplayer.url match.
    # Strip query params before matching since their URLs are stored canonical.
    canonical = full_url.split("?", 1)[0]
    resp = request_with_backoff(
        "GET",
        "https://api.pokemontcg.io/v2/cards",
        params={"q": f'tcgplayer.url:"{canonical}"', "pageSize": 1},
        headers={"Accept": "application/json"},
    )
    if resp and resp.status_code == 200:
        items = (resp.json() or {}).get("data") or []
        if items:
            return _pokemontcg_card_to_result(items[0])
    return None


def _resolve_ygoprodeck_url(qs: Dict[str, List[str]]) -> Optional[Dict]:
    # /card/?search=<id-or-name>
    needle = (qs.get("search") or qs.get("id") or [None])[0]
    if not needle:
        return None
    params: Dict[str, str] = {"id": needle} if needle.isdigit() else {"name": needle}
    resp = request_with_backoff(
        "GET",
        "https://db.ygoprodeck.com/api/v7/cardinfo.php",
        params=params,
        headers={"Accept": "application/json"},
    )
    if not resp or resp.status_code != 200:
        return None
    items = (resp.json() or {}).get("data") or []
    if not items:
        return None
    return _ygoprodeck_card_to_result(items[0])


def _resolve_pokemontcg_url(path: str) -> Optional[Dict]:
    # PokemonTCG.io public site URL pattern includes the card id at the tail.
    m = re.search(r"/cards?/([\w-]+)$", path)
    if not m:
        return None
    resp = request_with_backoff(
        "GET",
        f"https://api.pokemontcg.io/v2/cards/{m.group(1)}",
        headers={"Accept": "application/json"},
    )
    if not resp or resp.status_code != 200:
        return None
    return _pokemontcg_card_to_result((resp.json() or {}).get("data") or {})


# Helpers shared with search() so search results and resolved URLs use the same shape.

def _scryfall_card_to_result(card: Dict) -> Dict:
    prices = card.get("prices") or {}
    image = (card.get("image_uris") or {}).get("small") or (
        (card.get("card_faces") or [{}])[0].get("image_uris") or {}
    ).get("small")
    return {
        "external_source": "scryfall",
        "external_id": card.get("id", ""),
        "name": card.get("name", ""),
        "set_name": card.get("set_name", ""),
        "image_url": image,
        "tcgplayer_price": _safe_float(prices.get("usd")),
        "tcgplayer_price_foil": _safe_float(prices.get("usd_foil")),
        "rarity": card.get("rarity"),
    }


def _pokemontcg_card_to_result(card: Dict) -> Dict:
    tcg = ((card.get("tcgplayer") or {}).get("prices") or {})
    normal = _safe_float((tcg.get("normal") or {}).get("market"))
    holo = _safe_float((tcg.get("holofoil") or {}).get("market"))
    return {
        "external_source": "pokemontcg",
        "external_id": card.get("id", ""),
        "name": card.get("name", ""),
        "set_name": (card.get("set") or {}).get("name", ""),
        "image_url": (card.get("images") or {}).get("small"),
        "tcgplayer_price": normal or holo,
        "tcgplayer_price_foil": holo,
        "rarity": card.get("rarity"),
    }


def _ygoprodeck_card_to_result(card: Dict) -> Dict:
    prices = (card.get("card_prices") or [{}])[0]
    sets = card.get("card_sets") or []
    return {
        "external_source": "ygoprodeck",
        "external_id": str(card.get("id", "")),
        "name": card.get("name", ""),
        "set_name": sets[0].get("set_name") if sets else "",
        "image_url": ((card.get("card_images") or [{}])[0]).get("image_url_small"),
        "tcgplayer_price": _safe_float(prices.get("tcgplayer_price")),
        "tcgplayer_price_foil": None,
        "rarity": card.get("type"),
    }


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
    return [_scryfall_card_to_result(c) for c in ((resp.json().get("data") or [])[:limit])]


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
    return [_pokemontcg_card_to_result(c) for c in ((resp.json().get("data") or [])[:limit])]


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
    return [_ygoprodeck_card_to_result(c) for c in ((resp.json().get("data") or [])[:limit])]


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
