"""CardMarket client — OAuth1 (MKM-Server-Sig).

CardMarket uses OAuth1 with HMAC-SHA1 signing. Reference:
https://api.cardmarket.com/ws/documentation/API_2.0:Auth_OAuth_Header

Implements signed-URL OAuth1 (no realm, no callback). For production, swap in mkmsdk
or oauth2 library if you prefer.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
import urllib.parse
from typing import Dict, Optional

from .base import PriceProvider, PriceQuery, ProviderResult, request_with_backoff

logger = logging.getLogger(__name__)

API_BASE = "https://api.cardmarket.com/ws/v2.0/output.json"

GAME_IDS = {
    "magic": 1,
    "yugioh": 3,
    "pokemon": 6,
}


class CardMarketProvider:
    name = "CardMarket"

    def __init__(self) -> None:
        self.app_token = os.environ.get("CARDMARKET_APP_TOKEN", "")
        self.app_secret = os.environ.get("CARDMARKET_APP_SECRET", "")
        self.access_token = os.environ.get("CARDMARKET_ACCESS_TOKEN", "")
        self.access_secret = os.environ.get("CARDMARKET_ACCESS_SECRET", "")

    def is_enabled(self) -> bool:
        return bool(self.app_token and self.app_secret and self.access_token and self.access_secret)

    def _sign(self, method: str, url: str) -> str:
        """Build the OAuth1 Authorization header with HMAC-SHA1 signature."""
        params: Dict[str, str] = {
            "oauth_consumer_key": self.app_token,
            "oauth_token": self.access_token,
            "oauth_nonce": secrets.token_hex(8),
            "oauth_timestamp": str(int(time.time())),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_version": "1.0",
        }
        # CardMarket signs the realm into the base string (the URL itself becomes the realm).
        encoded = "&".join(
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
            for k, v in sorted(params.items())
        )
        base_string = "&".join([
            method.upper(),
            urllib.parse.quote(url, safe=""),
            urllib.parse.quote(encoded, safe=""),
        ])
        signing_key = f"{urllib.parse.quote(self.app_secret, safe='')}&{urllib.parse.quote(self.access_secret, safe='')}"
        signature = base64.b64encode(
            hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
        ).decode()
        params["oauth_signature"] = signature
        header_parts = [f'realm="{url}"'] + [
            f'{k}="{urllib.parse.quote(v, safe="")}"' for k, v in params.items()
        ]
        return "OAuth " + ", ".join(header_parts)

    def fetch(self, query: PriceQuery) -> ProviderResult:
        if not self.is_enabled():
            return ProviderResult(self.name, None)

        game_id = GAME_IDS.get(query.game.lower())
        if not game_id:
            return ProviderResult(self.name, None)

        # Search products
        search_url = f"{API_BASE}/products/find"
        # Note: we don't sign query params into the URL for simplicity. CardMarket allows it
        # if the URL exactly matches what's signed. We append params to the signed URL.
        full_url = (
            f"{search_url}?search={urllib.parse.quote(query.name)}"
            f"&idGame={game_id}&exact=false&start=0&maxResults=5"
        )
        auth = self._sign("GET", full_url)
        resp = request_with_backoff(
            "GET",
            full_url,
            headers={"Authorization": auth, "Accept": "application/json"},
        )
        if not resp or resp.status_code >= 400:
            return ProviderResult(self.name, None)
        try:
            products = resp.json().get("product", [])
        except ValueError:
            return ProviderResult(self.name, None)
        if not products:
            return ProviderResult(self.name, None)

        product = products[0]
        price_guide = product.get("priceGuide", {}) if isinstance(product, dict) else {}
        # priceGuide keys: SELL, LOW, AVG, TREND, etc. Prefer TREND -> AVG -> LOW.
        for key in ("TREND", "AVG", "LOW", "SELL"):
            if price_guide.get(key) is not None:
                try:
                    return ProviderResult(self.name, float(price_guide[key]), raw=price_guide)
                except (TypeError, ValueError):
                    continue
        return ProviderResult(self.name, None)
