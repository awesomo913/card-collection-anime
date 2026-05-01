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


def search(query: str, game: str, limit: int = 12, sealed: bool = False) -> List[Dict]:
    """Dispatch to the per-game catalog. Returns normalized CatalogResult dicts.

    When ``sealed=True`` we filter Scryfall to ``is:sealed``. The Pokemon and
    YGO public APIs only carry single cards, so sealed search returns [].
    """
    g = (game or "").lower()
    if g == "magic":
        return _search_scryfall(query, limit, sealed=sealed)
    if sealed:
        return []
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
    m = re.match(r"^/product/(\d+)(?:/([^/?#]+))?", path)
    if not m:
        return None
    product_id = m.group(1)
    slug = (m.group(2) or "").lower()

    # 1. Magic: Scryfall has /cards/tcgplayer/<id>.
    resp = request_with_backoff(
        "GET",
        f"https://api.scryfall.com/cards/tcgplayer/{product_id}",
        headers={"User-Agent": "card-collection-anime/1.0", "Accept": "application/json"},
    )
    if resp and resp.status_code == 200:
        return _scryfall_card_to_result(resp.json())

    # 2. Pokemon: PokemonTCG.io supports filtering by tcgplayer.url match.
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

    # 3. Yu-Gi-Oh slug -> search YGOPRODeck by extracted name. YGOPRODeck's
    # fname is a strict substring match against the canonical card name, so a
    # hyphenated card (Red-Eyes Dark Dragoon) won't match "red eyes dark dragoon".
    # Walk shrinking trailing N-grams to find the tightest substring that hits.
    name = _slug_to_card_name(slug)
    if name and ("yugioh" in slug or "yu-gi-oh" in slug):
        tokens = name.split()
        for n in (len(tokens), 3, 2, 1):
            if n <= 0 or n > len(tokens):
                continue
            cand = " ".join(tokens[-n:])
            rows = _search_ygoprodeck(cand, limit=5)
            if rows:
                # Prefer the row whose lowercased name contains the most query tokens.
                target = set(tokens)
                rows.sort(
                    key=lambda r: -len(target & set((r.get("name") or "").lower().replace("-", " ").split()))
                )
                return rows[0]
    if name and "pokemon" in slug:
        rows = _search_pokemontcg(name, limit=1)
        if rows:
            return rows[0]

    # 4. Last resort — scrape the TCGplayer page itself for OG metadata.
    return _scrape_tcgplayer_og(full_url, product_id)


def _slug_to_card_name(slug: str) -> str:
    """Extract a probable card name from a TCGplayer URL slug.

    Pattern is roughly ``<game>-<set-tokens>-<set-number>-<card-name-tokens>``
    for Yu-Gi-Oh and ``<game>-<set-tokens>-<card-name-tokens>-<num>`` for Pokemon.
    Strategy: drop the game prefix, find the first numeric token; tokens after
    it are usually the card name. If nothing's after the digit, fall back to
    the trailing tokens before it.
    """
    if not slug:
        return ""
    parts = slug.split("-")
    if parts and parts[0] in {"yugioh", "yu", "pokemon", "magic", "mtg"}:
        # 'yu-gi-oh' splits into ['yu','gi','oh',...] — drop those too
        if parts[0] == "yu" and len(parts) >= 3 and parts[1] == "gi" and parts[2] == "oh":
            parts = parts[3:]
        else:
            parts = parts[1:]
    digit_idx = next((i for i, p in enumerate(parts) if p.isdigit()), -1)
    if digit_idx >= 0 and digit_idx < len(parts) - 1:
        return " ".join(parts[digit_idx + 1 :])
    if digit_idx > 0:
        return " ".join(parts[max(0, digit_idx - 3) : digit_idx])
    return " ".join(parts)


_META_TAG_RE = re.compile(r"<meta\b([^>]*)>", re.IGNORECASE)
_META_ATTR_PROP_RE = re.compile(
    r"""(?:property|name|itemprop)\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)
_META_ATTR_CONTENT_RE = re.compile(r"""content\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_JSON_LD_PRICE_RE = re.compile(
    r'"price"\s*:\s*"?(\d+(?:\.\d+)?)"?', re.IGNORECASE
)


def _parse_meta_tags(html: str) -> Dict[str, str]:
    """Pull og: / twitter: / product:price tags out of HTML regardless of attribute order."""
    out: Dict[str, str] = {}
    for tag in _META_TAG_RE.findall(html):
        prop = _META_ATTR_PROP_RE.search(tag)
        content = _META_ATTR_CONTENT_RE.search(tag)
        if not (prop and content):
            continue
        key = prop.group(1).lower()
        if key.startswith(("og:", "twitter:", "product:")):
            out.setdefault(key, content.group(1))
    return out


def _scrape_tcgplayer_og(url: str, product_id: str) -> Optional[Dict]:
    """Pull og:title / og:image / price from a TCGplayer product page. No API
    key required; used as a last-resort resolver for any TCGplayer URL."""
    resp = request_with_backoff(
        "GET",
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=10.0,
    )
    if not resp or resp.status_code >= 400:
        return None
    html = (resp.text or "")[:300_000]
    tags = _parse_meta_tags(html)
    title = tags.get("og:title") or tags.get("twitter:title")
    image = tags.get("og:image") or tags.get("twitter:image")
    price = _safe_float(tags.get("product:price:amount") or tags.get("og:price:amount"))
    if price is None:
        # JSON-LD price hint — most retail sites embed a Product schema.
        m = _JSON_LD_PRICE_RE.search(html)
        if m:
            price = _safe_float(m.group(1))
    if not title:
        return None
    return {
        "external_source": "tcgplayer",
        "external_id": product_id,
        "name": title.strip(),
        "set_name": "",
        "image_url": image or None,
        "tcgplayer_price": price,
        "tcgplayer_price_foil": None,
        "rarity": None,
    }


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
        if external_source == "tcgplayer":
            # Re-scrape the product page to pick up any current OG price tag.
            scraped = _scrape_tcgplayer_og(
                f"https://www.tcgplayer.com/product/{external_id}", external_id
            )
            return scraped.get("tcgplayer_price") if scraped else None
    except Exception as exc:
        logger.warning("Catalog price refresh failed (%s/%s): %s", external_source, external_id, exc)
    return None


# ---------------------------------------------------------------------------
# Scryfall (Magic: The Gathering)
# ---------------------------------------------------------------------------

def _search_scryfall(query: str, limit: int, sealed: bool = False) -> List[Dict]:
    q = f"{query} is:sealed" if sealed else query
    resp = request_with_backoff(
        "GET",
        "https://api.scryfall.com/cards/search",
        params={"q": q, "unique": "prints", "order": "released"},
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
