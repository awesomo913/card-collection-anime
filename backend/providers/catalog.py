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
from typing import Dict, List, Optional, Tuple
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


def _tcgplayer_product_details(product_id: str) -> Optional[Dict]:
    """Hit TCGplayer's own product-details API. No auth required — this is the
    same endpoint the public product page uses to populate prices."""
    resp = request_with_backoff(
        "GET",
        f"https://mp-search-api.tcgplayer.com/v1/product/{product_id}/details",
        params={"mpfev": "2779"},
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Origin": "https://www.tcgplayer.com",
            "Referer": "https://www.tcgplayer.com/",
        },
        timeout=8.0,
    )
    if not resp or resp.status_code >= 400:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _result_from_tcgplayer_details(product_id: str, details: Dict) -> Dict:
    """Build a minimal CatalogResult from TCGplayer's own product API."""
    name = details.get("productUrlName") or details.get("productName") or ""
    if name:
        name = name.replace(" - ", " — ").strip()
    image_url = (
        f"https://product-images.tcgplayer.com/fit-in/437x437/{product_id}.jpg"
    )
    return {
        "external_source": "tcgplayer",
        "external_id": str(product_id),
        "name": name,
        "set_name": details.get("setUrlName") or details.get("setName") or "",
        "image_url": image_url,
        "tcgplayer_price": _safe_float(details.get("marketPrice")),
        "tcgplayer_price_foil": None,
        "rarity": details.get("rarityName"),
    }


def _resolve_tcgplayer_url(path: str, full_url: str) -> Optional[Dict]:
    m = re.match(r"^/product/(\d+)(?:/([^/?#]+))?", path)
    if not m:
        return None
    product_id = m.group(1)
    slug = (m.group(2) or "").lower()

    # Always pre-fetch TCGplayer's own product details. We use these as the
    # authoritative price source (the per-game catalogs return aggregate
    # prices that don't reflect the specific printing the URL points at).
    tcg_details = _tcgplayer_product_details(product_id)
    tcg_price = _safe_float((tcg_details or {}).get("marketPrice"))

    # 1. Magic: Scryfall has /cards/tcgplayer/<id>.
    resp = request_with_backoff(
        "GET",
        f"https://api.scryfall.com/cards/tcgplayer/{product_id}",
        headers={"User-Agent": "card-collection-anime/1.0", "Accept": "application/json"},
    )
    if resp and resp.status_code == 200:
        out = _scryfall_card_to_result(resp.json())
        if tcg_price is not None:
            out["tcgplayer_price"] = tcg_price
        return out

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
            out = _pokemontcg_card_to_result(items[0])
            if tcg_price is not None:
                out["tcgplayer_price"] = tcg_price
            return out

    # 3. Yu-Gi-Oh slug -> search YGOPRODeck by extracted name. YGOPRODeck's
    # fname is a strict substring match against the canonical card name, so a
    # hyphenated card (Red-Eyes Dark Dragoon) won't match "red eyes dark dragoon".
    # Walk shrinking trailing N-grams to find the tightest substring that hits,
    # then pin the printing whose set_name overlaps with the slug's set tokens.
    set_tokens_str, name = _split_slug(slug)
    # Keep digits — they're often the disambiguator between similarly-named
    # printings (e.g. "Rarity Collection 5" vs "25th Anniversary Rarity Collection II").
    set_tokens = set_tokens_str.split()
    if name and ("yugioh" in slug or "yu-gi-oh" in slug):
        tokens = name.split()
        # YGOPRODeck returns the raw card payload — we need the per-printing
        # detail in card_sets, so call the underlying API directly.
        for n in (len(tokens), 3, 2, 1):
            if n <= 0 or n > len(tokens):
                continue
            cand = " ".join(tokens[-n:])
            cards = _ygoprodeck_card_payloads(cand, limit=5)
            if not cards:
                continue
            target = set(tokens)
            cards.sort(
                key=lambda c: -len(target & set((c.get("name") or "").lower().replace("-", " ").split()))
            )
            best = cards[0]
            # Pick the printing closest to the URL's set tokens.
            out = _ygoprodeck_card_to_result(best, preferred_set_tokens=set_tokens)
            if tcg_price is not None:
                out["tcgplayer_price"] = tcg_price
            if (tcg_details or {}).get("rarityName"):
                out["rarity"] = tcg_details["rarityName"]
            return out
    if name and "pokemon" in slug:
        rows = _search_pokemontcg(name, limit=1)
        if rows:
            out = rows[0]
            if tcg_price is not None:
                out["tcgplayer_price"] = tcg_price
            return out

    # 4. TCGplayer's own product API as a primary fallback — gives us name +
    # marketPrice + rarity even when none of the per-game catalogs know about
    # this product (sealed boxes, presale items, etc.).
    if tcg_details:
        return _result_from_tcgplayer_details(product_id, tcg_details)

    # 5. Last resort — scrape the TCGplayer page itself for OG metadata.
    return _scrape_tcgplayer_og(full_url, product_id)


def _split_slug(slug: str) -> Tuple[str, str]:
    """Split a TCGplayer URL slug into (set_name_tokens, card_name_tokens).

    YGO/MTG pattern is ``<game>-<set-words>-<set-num>-<card-words>``; Pokemon is
    ``<game>-<set-words>-<card-words>-<num>``. We use the first numeric token as
    the divider — tokens before it are the set, tokens after are the card name.
    Returns lowercase space-joined strings (empty when missing).
    """
    if not slug:
        return "", ""
    parts = slug.split("-")
    if parts and parts[0] in {"yugioh", "yu", "pokemon", "magic", "mtg"}:
        if parts[0] == "yu" and len(parts) >= 3 and parts[1] == "gi" and parts[2] == "oh":
            parts = parts[3:]
        else:
            parts = parts[1:]
    digit_idx = next((i for i, p in enumerate(parts) if p.isdigit()), -1)
    if digit_idx >= 0 and digit_idx < len(parts) - 1:
        # YGO style: set tokens before the digit (digit itself often is part of
        # the set, e.g. "Rarity Collection 5"), name tokens after.
        return " ".join(parts[: digit_idx + 1]), " ".join(parts[digit_idx + 1 :])
    if digit_idx > 0:
        # Pokemon-ish style: trailing digit is a collector number; everything
        # before is set+card with no clean split. Best-effort: last 3 tokens
        # are name, rest is set.
        cut = max(0, digit_idx - 3)
        return " ".join(parts[:cut]), " ".join(parts[cut:digit_idx])
    return "", " ".join(parts)


def _slug_to_card_name(slug: str) -> str:
    return _split_slug(slug)[1]


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


def _ygoprodeck_card_to_result(card: Dict, preferred_set_tokens: Optional[List[str]] = None) -> Dict:
    prices = (card.get("card_prices") or [{}])[0]
    sets = card.get("card_sets") or []
    chosen = _pick_yugioh_printing(sets, preferred_set_tokens or [])
    set_name = (chosen or {}).get("set_name", "") if chosen else (sets[0].get("set_name") if sets else "")
    # Per-printing price beats the card-wide aggregate when the user pinned a specific set.
    set_price = _safe_float((chosen or {}).get("set_price"))
    rarity = (chosen or {}).get("set_rarity") if chosen else None
    return {
        "external_source": "ygoprodeck",
        "external_id": str(card.get("id", "")),
        "name": card.get("name", ""),
        "set_name": set_name,
        "image_url": ((card.get("card_images") or [{}])[0]).get("image_url_small"),
        "tcgplayer_price": set_price if set_price is not None else _safe_float(prices.get("tcgplayer_price")),
        "tcgplayer_price_foil": None,
        "rarity": rarity or card.get("type"),
    }


def _pick_yugioh_printing(sets: List[Dict], target_tokens: List[str]) -> Optional[Dict]:
    """Pick the printing whose set_name shares the most tokens with the URL slug."""
    if not sets:
        return None
    if not target_tokens:
        return sets[0]
    target = set(t.lower() for t in target_tokens)
    scored = []
    for entry in sets:
        name = (entry.get("set_name") or "").lower().replace("-", " ")
        overlap = len(target & set(name.split()))
        scored.append((overlap, entry))
    scored.sort(key=lambda pair: -pair[0])
    # Only prefer the matched printing when there's actual overlap.
    return scored[0][1] if scored[0][0] > 0 else sets[0]


def _ygoprodeck_card_payloads(query: str, limit: int) -> List[Dict]:
    """Raw card dicts from YGOPRODeck (preserves card_sets so callers can pick a printing)."""
    resp = request_with_backoff(
        "GET",
        "https://db.ygoprodeck.com/api/v7/cardinfo.php",
        params={"fname": query, "num": limit, "offset": 0},
        headers={"Accept": "application/json"},
    )
    if not resp or resp.status_code >= 400:
        return []
    return ((resp.json() or {}).get("data") or [])[:limit]


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
            # Hit TCGplayer's own product API — same data the public page uses.
            details = _tcgplayer_product_details(external_id)
            if details:
                return _safe_float(details.get("marketPrice"))
            return None
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
