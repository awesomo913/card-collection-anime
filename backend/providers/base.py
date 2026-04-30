"""Provider abstraction. Each marketplace implements PriceProvider."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Protocol

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceQuery:
    """Search input for a price lookup."""
    name: str
    set_name: str
    game: str  # magic, pokemon, yugioh
    is_foil: bool = False
    is_sealed: bool = False
    product_type: Optional[str] = None  # only for sealed: "booster box", "pack", etc.


@dataclass(frozen=True)
class ProviderResult:
    """Outcome of a price fetch — None price means lookup failed cleanly."""
    source: str
    price: Optional[float]
    raw: Optional[dict] = None


class PriceProvider(Protocol):
    """Interface every marketplace client implements."""
    name: str

    def is_enabled(self) -> bool: ...
    def fetch(self, query: PriceQuery) -> ProviderResult: ...


def request_with_backoff(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    json: Optional[dict] = None,
    data: Optional[dict] = None,
    timeout: float = 5.0,
    max_retries: int = 3,
) -> Optional[requests.Response]:
    """HTTP wrapper with exponential backoff on 429/5xx. Returns None on terminal failure."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                data=data,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            logger.warning("HTTP %s %s failed (attempt %s): %s", method, url, attempt + 1, exc)
            time.sleep(delay)
            delay *= 2
            continue

        if resp.status_code < 400:
            return resp
        if resp.status_code in (429, 500, 502, 503, 504):
            retry_after = resp.headers.get("Retry-After")
            sleep_for = float(retry_after) if retry_after and retry_after.isdigit() else delay
            logger.info("HTTP %s on %s; backing off %.1fs", resp.status_code, url, sleep_for)
            time.sleep(sleep_for)
            delay *= 2
            continue
        # 4xx other than 429 — not retryable
        logger.warning("HTTP %s on %s: %s", resp.status_code, url, resp.text[:200])
        return resp
    return None
