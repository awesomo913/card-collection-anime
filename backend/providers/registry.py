"""Provider registry and selection."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import List

from .base import PriceProvider
from .cardmarket import CardMarketProvider
from .ebay import EbayProvider
from .tcgplayer import TCGPlayerProvider

logger = logging.getLogger(__name__)

ALL_PROVIDERS = {
    "TCGPlayer": TCGPlayerProvider,
    "eBay": EbayProvider,
    "CardMarket": CardMarketProvider,
}


@lru_cache(maxsize=1)
def get_enabled_providers() -> List[PriceProvider]:
    """Return instantiated providers that are both whitelisted and credentialed."""
    enabled_env = os.environ.get("PRICE_SOURCES_ENABLED", "TCGPlayer,eBay,CardMarket")
    requested = [n.strip() for n in enabled_env.split(",") if n.strip()]
    out: List[PriceProvider] = []
    for name in requested:
        cls = ALL_PROVIDERS.get(name)
        if not cls:
            logger.warning("Unknown provider in PRICE_SOURCES_ENABLED: %s", name)
            continue
        instance = cls()
        if instance.is_enabled():
            logger.info("Enabled price provider: %s", name)
            out.append(instance)
        else:
            logger.info("Provider %s skipped — credentials missing", name)
    return out
