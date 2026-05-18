"""DeepSeek-powered short-term price forecasting for cards + sealed.

Design notes:
- Text-only LLM call (no images) — uses DeepSeekVision.chat_json().
- Per-item, on-demand from the detail page. Not auto-run for every card on
  every page load — token cost adds up fast on big collections.
- Server-side cache keyed by (item_type, item_id, last_history_timestamp).
  When a new PriceHistory row lands, the key naturally changes → next call
  triggers a fresh forecast. No manual invalidation needed.
- TTL on the cache is a defense-in-depth backstop (the timestamp key already
  invalidates on data change; TTL just guarantees we don't serve a 6-month-old
  forecast for a card whose scheduler stalled).
- Forecasts are SPECULATIVE. The frontend renders a disclaimer and the model
  is asked for low/high range + self-rated confidence, not single-point
  "guaranteed" numbers.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import schemas
import models
from providers.deepseek import DeepSeekVision, DeepSeekVisionError

logger = logging.getLogger(__name__)

# How long a forecast stays warm in the in-memory cache. Defensive backstop:
# the cache key already includes the latest PriceHistory timestamp so new
# data invalidates naturally. TTL guards against scheduler stalls (no new
# price would otherwise mean the same forecast forever).
CACHE_TTL_SECONDS = int(os.environ.get("FORECAST_CACHE_TTL", "86400"))  # 24h

# Cap on history rows we send to the model. More context = better forecast
# but also more tokens. 50 samples ≈ 12 days at 4×/day cadence which is
# plenty signal for a 7/30/90-day horizon projection.
MAX_HISTORY_SAMPLES = 50

# (item_type, item_id, last_history_ts_iso) → (cache_inserted_at, ForecastResult).
# Module-scope dict guarded by a lock; survives across requests in one process,
# resets at restart (forecasts are ephemeral so this is fine).
_cache: Dict[Tuple[str, int, str], Tuple[float, schemas.ForecastResult]] = {}
_cache_lock = threading.Lock()


SYSTEM_PROMPT = (
    "You are a TCG market analyst. Given a card's metadata and recent price "
    "history, project realistic short-term value ranges. "
    "Be conservative — most cards don't move much in 7 days. Strong moves "
    "usually come from set rotations, tournament results, format bans/unbans, "
    "or sudden player attention. "
    "Distinguish bull and bear cases in your reasoning. "
    "You ALWAYS respond with valid JSON matching the schema in the user "
    "message — no prose, no markdown fences, no caveats outside the JSON."
)


def _build_user_prompt(
    item_name: str,
    game: str,
    set_name: str,
    rarity: Optional[str],
    is_foil: bool,
    is_sealed: bool,
    product_type: Optional[str],
    current_price: Optional[float],
    acquired_price: Optional[float],
    purchase_price: Optional[float],
    history: List[Tuple[str, str, float]],  # (iso_ts, source, price)
) -> str:
    """Compose the per-item user prompt. History is rendered as a compact table."""
    lines: List[str] = []
    lines.append(f"Item: {item_name}")
    lines.append(f"Game: {game}")
    lines.append(f"Set: {set_name or 'unknown'}")
    if is_sealed:
        lines.append(f"Sealed product type: {product_type or 'unknown'}")
    else:
        lines.append(f"Rarity: {rarity or 'unknown'}")
        lines.append(f"Foil: {is_foil}")
    if current_price is not None:
        lines.append(f"Current TCGplayer price: ${current_price:.2f}")
    if acquired_price is not None:
        lines.append(f"Initial market price (when added): ${acquired_price:.2f}")
    if purchase_price is not None:
        lines.append(f"User paid: ${purchase_price:.2f}")
    lines.append("")
    lines.append(f"Recent price history (newest last, {len(history)} samples):")
    for ts, source, price in history:
        lines.append(f"  {ts}  {source:<12}  ${price:.2f}")
    lines.append("")
    lines.append(
        "Return JSON matching this exact schema:\n"
        "{\n"
        '  "horizons": [\n'
        '    {"days": 7,  "low": <float>, "high": <float>, "target": <float>, "confidence": 0.0-1.0},\n'
        '    {"days": 30, "low": <float>, "high": <float>, "target": <float>, "confidence": 0.0-1.0},\n'
        '    {"days": 90, "low": <float>, "high": <float>, "target": <float>, "confidence": 0.0-1.0}\n'
        '  ],\n'
        '  "direction": "up" | "down" | "flat",\n'
        '  "reasoning": "<2-3 sentences on what drives the projection>",\n'
        '  "drivers": ["<short bullet>", "<short bullet>"],\n'
        '  "caveats": ["<risks / why this could be wrong>"]\n'
        "}\n"
        "\n"
        "Rules:\n"
        "- All prices in USD.\n"
        "- low <= target <= high for each horizon.\n"
        "- If you have too little signal to project, set confidence ≤ 0.3 and "
        "say so in caveats.\n"
        "- Don't invent news. Only cite drivers you can infer from the history "
        "trend, the game/set/rarity context, or widely-known TCG dynamics."
    )
    return "\n".join(lines)


def _coerce_forecast_body(
    raw: dict,
    item_type: str,
    item_id: int,
    item_name: str,
    current_price: Optional[float],
    model: str,
    history_count: int,
) -> schemas.ForecastResult:
    """Parse the model's JSON into a strict ForecastResult.

    Sanitises: enforces low<=target<=high, clamps confidence, normalises
    direction tag, caps reasoning at 600 chars (LLM occasionally over-shares).
    """
    horizons_raw = raw.get("horizons") or []
    horizons: List[schemas.ForecastHorizon] = []
    if isinstance(horizons_raw, list):
        for h in horizons_raw:
            if not isinstance(h, dict):
                continue
            try:
                days = int(h.get("days", 0))
                low = max(0.0, float(h.get("low", 0)))
                high = max(0.0, float(h.get("high", 0)))
                target = max(0.0, float(h.get("target", 0)))
                conf = max(0.0, min(1.0, float(h.get("confidence", 0))))
            except (TypeError, ValueError):
                continue
            if days < 1 or days > 730:
                continue
            # Enforce ordering — model sometimes flips them.
            low, high = (low, high) if low <= high else (high, low)
            if not (low <= target <= high):
                target = (low + high) / 2.0
            horizons.append(schemas.ForecastHorizon(
                days=days, low=round(low, 2), high=round(high, 2),
                target=round(target, 2), confidence=round(conf, 2),
            ))

    direction_raw = str(raw.get("direction") or "").strip().lower()
    direction = direction_raw if direction_raw in {"up", "down", "flat"} else "unknown"

    drivers = [str(d).strip() for d in (raw.get("drivers") or []) if str(d).strip()][:5]
    caveats = [str(c).strip() for c in (raw.get("caveats") or []) if str(c).strip()][:5]
    reasoning = str(raw.get("reasoning") or "").strip()[:600]

    return schemas.ForecastResult(
        item_type=item_type,
        item_id=item_id,
        item_name=item_name,
        current_price=current_price,
        horizons=horizons,
        direction=direction,
        reasoning=reasoning,
        drivers=drivers,
        caveats=caveats,
        generated_at=datetime.utcnow(),
        model=model,
        history_samples_used=history_count,
        cached=False,
    )


def _cache_key(item_type: str, item_id: int, history: List[models.PriceHistory]) -> Tuple[str, int, str]:
    """Cache key = (type, id, latest_history_ts). Naturally invalidates on new data."""
    last_ts = history[-1].timestamp.isoformat() if history else "no-history"
    return (item_type, item_id, last_ts)


def _serialise_history(history: List[models.PriceHistory]) -> List[Tuple[str, str, float]]:
    """Convert PriceHistory ORM rows → compact tuples for prompt rendering."""
    rows = [
        (h.timestamp.isoformat(timespec="minutes"), h.source, float(h.price))
        for h in history
    ]
    # Trim to most recent MAX_HISTORY_SAMPLES (history is already sorted oldest→newest).
    if len(rows) > MAX_HISTORY_SAMPLES:
        rows = rows[-MAX_HISTORY_SAMPLES:]
    return rows


def forecast_item(
    client: DeepSeekVision,
    item_type: str,                  # "card" | "sealed"
    item: models.Card | models.SealedProduct,
    history: List[models.PriceHistory],
) -> schemas.ForecastResult:
    """Return a forecast for this item, served from cache when possible.

    Caller is responsible for fetching the ORM rows; this function does only
    the prompt + model call + cache management. Pure-ish wrt the DB (read-only
    on the inputs).
    """
    cache_key = _cache_key(item_type, item.id, history)
    now = time.monotonic()

    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit is not None:
            inserted_at, cached_result = hit
            if (now - inserted_at) <= CACHE_TTL_SECONDS:
                logger.info(
                    "forecast cache HIT item=%s/%s age=%.0fs",
                    item_type, item.id, now - inserted_at,
                )
                # Return a copy with cached=True so the UI can label it.
                return cached_result.model_copy(update={"cached": True})

    is_sealed = item_type == "sealed"
    user_prompt = _build_user_prompt(
        item_name=item.name,
        game=item.game or "unknown",
        set_name=item.set_name or "",
        rarity=getattr(item, "rarity", None),
        is_foil=bool(getattr(item, "is_foil", False)),
        is_sealed=is_sealed,
        product_type=getattr(item, "product_type", None) if is_sealed else None,
        current_price=item.current_price,
        acquired_price=getattr(item, "acquired_price", None),
        purchase_price=item.purchase_price,
        history=_serialise_history(history),
    )

    started = time.monotonic()
    try:
        ds_result = client.chat_json(SYSTEM_PROMPT, user_prompt)
    except DeepSeekVisionError as exc:
        logger.warning("forecast deepseek failed item=%s/%s: %s", item_type, item.id, exc)
        # Return an empty-horizon result with the error surfaced in caveats so
        # the UI degrades gracefully rather than 500ing.
        return schemas.ForecastResult(
            item_type=item_type,
            item_id=item.id,
            item_name=item.name,
            current_price=item.current_price,
            horizons=[],
            direction="unknown",
            reasoning="",
            drivers=[],
            caveats=[f"Forecast unavailable: {exc}"],
            generated_at=datetime.utcnow(),
            model="(none)",
            history_samples_used=len(history),
            cached=False,
        )

    try:
        body = json.loads(ds_result.raw_content)
    except json.JSONDecodeError as exc:
        logger.warning(
            "forecast parse failed item=%s/%s content=%s",
            item_type, item.id, ds_result.raw_content[:200],
        )
        return schemas.ForecastResult(
            item_type=item_type,
            item_id=item.id,
            item_name=item.name,
            current_price=item.current_price,
            horizons=[],
            direction="unknown",
            caveats=[f"Model output unparseable: {exc}"],
            generated_at=datetime.utcnow(),
            model=ds_result.model,
            history_samples_used=len(history),
            cached=False,
        )

    result = _coerce_forecast_body(
        body, item_type, item.id, item.name, item.current_price,
        ds_result.model, len(history),
    )
    elapsed_ms = (time.monotonic() - started) * 1000.0
    logger.info(
        "forecast ok item=%s/%s horizons=%s ms=%.0f tokens_in=%s tokens_out=%s",
        item_type, item.id, len(result.horizons), elapsed_ms,
        ds_result.prompt_tokens, ds_result.completion_tokens,
    )

    with _cache_lock:
        _cache[cache_key] = (now, result)
    return result


def clear_cache() -> int:
    """Test helper / admin-action. Returns number of entries dropped."""
    with _cache_lock:
        n = len(_cache)
        _cache.clear()
    return n
