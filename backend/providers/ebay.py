"""eBay client — Browse API.

Auth: app-level OAuth bearer token (Client Credentials grant — see eBay developer docs).
Endpoint: GET https://api.ebay.com/buy/browse/v1/item_summary/search?q={query}
Reference: https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search
"""
from __future__ import annotations

import logging
import os
import statistics
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from .base import PriceProvider, PriceQuery, ProviderResult, request_with_backoff

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


def _is_ebay_url(url: str) -> bool:
    """True only for https links whose host is ebay.com or a subdomain.

    Listing URLs come from an external API and are rendered as clickable links,
    so we treat them as untrusted and drop anything that isn't clearly eBay.
    """
    try:
        parsed = urlparse(url or "")
    except (ValueError, TypeError):
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host == "ebay.com" or host.endswith(".ebay.com")


def _safe_image_url(url) -> Optional[str]:
    """Allow only https image URLs. eBay images live on i.ebayimg.com (not
    *.ebay.com), so _is_ebay_url is too strict — but the value is rendered in an
    <img> tag, so we still block non-https / javascript: / data: URIs."""
    return url if isinstance(url, str) and url.startswith("https://") else None


def _tiers_from_prices(prices: list) -> Optional[dict]:
    """Outlier-resistant low/mid/high/market from a listing price set.

    eBay name-search returns adjacent/wrong cards + multi-card lots, so raw
    min/max would be junk (a $1 wrong card, a $99 bundle). Use quartiles when
    there's enough data — low=Q1, mid/market=median, high=Q3 — so the extreme
    cheap-wrong-card and expensive-lot listings fall outside Q1..Q3. Falls back
    to min/max only when there are too few points (<4) to trim meaningfully.
    """
    if not prices:
        return None
    ordered = sorted(prices)
    median = statistics.median(ordered)
    if len(ordered) >= 4:
        q1, _q2, q3 = statistics.quantiles(ordered, n=4)
        low, high = q1, q3
    else:
        low, high = ordered[0], ordered[-1]
    return {
        "low": round(float(low), 2),
        "mid": round(float(median), 2),
        "high": round(float(high), 2),
        "market": round(float(median), 2),
    }

CATEGORY_IDS = {
    # eBay Trading Cards categories
    "magic": "183454",
    "pokemon": "183454",
    "yugioh": "183454",
}


class EbayProvider:
    name = "eBay"

    def __init__(self) -> None:
        self.client_id = os.environ.get("EBAY_CLIENT_ID", "")
        self.client_secret = os.environ.get("EBAY_CLIENT_SECRET", "")
        self.static_token = os.environ.get("EBAY_OAUTH_TOKEN", "")
        self.marketplace = os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US")
        self._token: Optional[str] = self.static_token or None
        self._token_expires_at: float = float("inf") if self.static_token else 0.0
        self._lock = threading.Lock()

    def is_enabled(self) -> bool:
        return bool(self.static_token or (self.client_id and self.client_secret))

    def _get_token(self) -> Optional[str]:
        with self._lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token
            if not (self.client_id and self.client_secret):
                return None
            import base64
            creds = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            resp = request_with_backoff(
                "POST",
                TOKEN_URL,
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": "https://api.ebay.com/oauth/api_scope",
                },
            )
            if not resp or resp.status_code >= 400:
                logger.error("eBay token request failed")
                return None
            data = resp.json()
            self._token = data.get("access_token")
            self._token_expires_at = time.time() + float(data.get("expires_in", 7200))
            return self._token

    def _search_items(self, query: PriceQuery, limit: int = 20) -> list:
        """Run the Browse API item-summary search; return raw itemSummaries (or [])."""
        if not self.is_enabled():
            return []
        token = self._get_token()
        if not token:
            return []

        q_parts = [query.name, query.set_name]
        if query.is_foil:
            q_parts.append("foil")
        if query.is_sealed and query.product_type:
            q_parts.append(query.product_type)
        q = " ".join(p for p in q_parts if p).strip()

        params = {
            "q": q,
            "limit": str(limit),
            "filter": "buyingOptions:{FIXED_PRICE},conditions:{NEW|USED}",
        }
        category_id = CATEGORY_IDS.get(query.game.lower())
        if category_id:
            params["category_ids"] = category_id

        resp = request_with_backoff(
            "GET",
            SEARCH_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
                "Accept": "application/json",
            },
            params=params,
        )
        if not resp or resp.status_code >= 400:
            return []
        data = resp.json()
        return data.get("itemSummaries", []) if isinstance(data, dict) else []

    def fetch(self, query: PriceQuery) -> ProviderResult:
        items = self._search_items(query)
        prices = []
        for item in items:
            price_obj = item.get("price") or {}
            try:
                value = float(price_obj.get("value"))
                if value > 0:
                    prices.append(value)
            except (TypeError, ValueError):
                continue
        if not prices:
            return ProviderResult(self.name, None)

        # Median is more robust to outliers than mean for marketplace listings.
        median = statistics.median(prices)
        return ProviderResult(
            self.name, round(float(median), 2),
            raw={"sample_size": len(prices)},
            tiers=_tiers_from_prices(prices),
        )

    def fetch_listings(self, query: PriceQuery, limit: int = 10) -> list:
        """Return individual eBay listings instead of a collapsed median.

        Each listing: ``{title, price, currency, condition, url, image}``. Only
        ``https://*.ebay.com`` item URLs survive — the rest are dropped as
        untrusted external content. Never raises: returns [] when disabled or
        on any upstream failure.
        """
        items = self._search_items(query, limit=limit)
        listings = []
        for item in items:
            url = item.get("itemWebUrl") or ""
            if not _is_ebay_url(url):
                continue
            price_obj = item.get("price") or {}
            try:
                price = float(price_obj.get("value"))
            except (TypeError, ValueError):
                price = None
            image_obj = item.get("image") or {}
            listings.append({
                "title": item.get("title") or "(untitled)",
                "price": round(price, 2) if price is not None else None,
                "currency": price_obj.get("currency"),
                "condition": item.get("condition"),
                "url": url,
                "image": _safe_image_url(image_obj.get("imageUrl")),
            })
            if len(listings) >= limit:
                break
        return listings
