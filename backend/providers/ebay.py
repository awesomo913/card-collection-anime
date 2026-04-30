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

from .base import PriceProvider, PriceQuery, ProviderResult, request_with_backoff

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

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

    def fetch(self, query: PriceQuery) -> ProviderResult:
        if not self.is_enabled():
            return ProviderResult(self.name, None)
        token = self._get_token()
        if not token:
            return ProviderResult(self.name, None)

        q_parts = [query.name, query.set_name]
        if query.is_foil:
            q_parts.append("foil")
        if query.is_sealed and query.product_type:
            q_parts.append(query.product_type)
        q = " ".join(p for p in q_parts if p).strip()

        params = {
            "q": q,
            "limit": "20",
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
            return ProviderResult(self.name, None)
        data = resp.json()
        items = data.get("itemSummaries", []) if isinstance(data, dict) else []
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
        return ProviderResult(self.name, round(float(median), 2), raw={"sample_size": len(prices)})
