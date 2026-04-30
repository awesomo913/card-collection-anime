"""TCGPlayer client — OAuth2 client_credentials flow with token caching.

Auth: POST https://api.tcgplayer.com/token with grant_type=client_credentials.
Search: GET /v1.39.0/catalog/categories/{categoryId}/search?productName={name}
Pricing: GET /v1.39.0/pricing/product/{productIds}
Reference: https://docs.tcgplayer.com/reference
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from .base import PriceProvider, PriceQuery, ProviderResult, request_with_backoff

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.tcgplayer.com/token"
API_BASE = "https://api.tcgplayer.com/v1.39.0"

# TCGPlayer category IDs (stable per TCGPlayer docs)
CATEGORY_IDS = {
    "magic": 1,
    "yugioh": 2,
    "pokemon": 3,
}


class TCGPlayerProvider:
    name = "TCGPlayer"

    def __init__(self) -> None:
        self.client_id = os.environ.get("TCGPLAYER_CLIENT_ID", "")
        self.client_secret = os.environ.get("TCGPLAYER_CLIENT_SECRET", "")
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()

    def is_enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _get_token(self) -> Optional[str]:
        with self._lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token
            resp = request_with_backoff(
                "POST",
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if not resp or resp.status_code >= 400:
                logger.error("TCGPlayer token request failed")
                return None
            data = resp.json()
            self._token = data.get("access_token")
            expires_in = float(data.get("expires_in", 1209600))  # default 14 days
            self._token_expires_at = time.time() + expires_in
            return self._token

    def fetch(self, query: PriceQuery) -> ProviderResult:
        if not self.is_enabled():
            return ProviderResult(self.name, None)
        token = self._get_token()
        if not token:
            return ProviderResult(self.name, None)

        category_id = CATEGORY_IDS.get(query.game.lower())
        if not category_id:
            return ProviderResult(self.name, None)

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        # Search for product
        search_resp = request_with_backoff(
            "GET",
            f"{API_BASE}/catalog/categories/{category_id}/search",
            headers=headers,
            params={"productName": query.name, "setName": query.set_name},
        )
        if not search_resp or search_resp.status_code >= 400:
            return ProviderResult(self.name, None)
        search_data = search_resp.json()
        product_ids = search_data.get("results", []) if isinstance(search_data, dict) else []
        if not product_ids:
            return ProviderResult(self.name, None)

        # Pricing endpoint accepts comma-separated product IDs (limit 250)
        ids_str = ",".join(str(pid) for pid in product_ids[:1])
        price_resp = request_with_backoff(
            "GET",
            f"{API_BASE}/pricing/product/{ids_str}",
            headers=headers,
        )
        if not price_resp or price_resp.status_code >= 400:
            return ProviderResult(self.name, None)
        price_data = price_resp.json()
        results = price_data.get("results", []) if isinstance(price_data, dict) else []

        # Pick foil or normal sub-type per query
        target_subtype = "Foil" if query.is_foil else "Normal"
        for entry in results:
            if entry.get("subTypeName") == target_subtype:
                price = entry.get("marketPrice") or entry.get("midPrice") or entry.get("lowPrice")
                if price is not None:
                    return ProviderResult(self.name, float(price), raw=entry)

        # Fallback: first non-null price
        for entry in results:
            price = entry.get("marketPrice") or entry.get("midPrice") or entry.get("lowPrice")
            if price is not None:
                return ProviderResult(self.name, float(price), raw=entry)

        return ProviderResult(self.name, None)
