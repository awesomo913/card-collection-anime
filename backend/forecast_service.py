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
import math
import os
import statistics
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
# but also more tokens. 100 samples ≈ 25 days at 4×/day cadence — DeepSeek
# V4 has a 128k context window so we have tons of headroom; doubled from 50
# specifically to give the volatility/trend computations more signal.
MAX_HISTORY_SAMPLES = 100

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
    "message — no prose, no markdown fences, no caveats outside the JSON. "
    "If you cannot meaningfully project (insufficient data, brand-new product, "
    "no comparable items), STILL return three horizons with confidence ≤ 0.2 "
    "and explain WHY in the caveats list — NEVER return an empty JSON object, "
    "an empty horizons array, or refuse to respond. "
    "\n\n"
    "CONFIDENCE CALIBRATION RUBRIC (apply per-horizon as hard rules — the "
    "server will clamp your output to enforce these ceilings, so calibrate "
    "honestly):\n"
    "  - Confidence ≥0.7 ALLOWED only when: sample_count ≥20 AND CV ≤0.08 "
    "AND source agreement is 'tight' or 'loose'.\n"
    "  - Confidence ≤0.3 MANDATORY when: sample_count <10 OR CV ≥0.20 OR "
    "source agreement is 'wide'.\n"
    "  - Confidence ≤0.2 MANDATORY when: sample_count <5 OR days_covered <3.\n"
    "  - Mid-band 0.3-0.7 otherwise, calibrated by signal consistency.\n"
    "  - Longer horizons (90d) generally lower-confidence than near-term (7d) "
    "when other signals are equal — even with deep history, the market gets "
    "more uncertain further out."
)


def _compute_history_metrics(
    history: List[Tuple[str, str, float]],
) -> Dict[str, float]:
    """Volatility / trend / breadth signals computed from the price series.

    The values feed two places: the prompt (so the model can calibrate its own
    confidence claim) and the post-hoc rubric clamp (so we enforce a hard
    ceiling regardless of what the model says). All outputs are JSON-safe
    floats; defaults are chosen so an empty/single-sample series still
    flows through downstream code without None-checks.
    """
    prices = [p for _, _, p in history]
    n = len(prices)
    if n == 0:
        return {
            "sample_count": 0, "days_covered": 0.0, "mean": 0.0, "median": 0.0,
            "stdev": 0.0, "cv": 0.0, "max_step_pct": 0.0,
            "trend_slope_pct_per_day": 0.0, "recent_vs_old_ratio": 1.0,
        }
    mean = statistics.fmean(prices)
    median = statistics.median(prices)
    stdev = statistics.pstdev(prices) if n > 1 else 0.0
    cv = (stdev / mean) if mean > 0 else 0.0

    max_step = 0.0
    for i in range(1, n):
        prev = prices[i - 1]
        if prev > 0:
            step = abs(prices[i] - prev) / prev
            if step > max_step:
                max_step = step

    try:
        first_ts = datetime.fromisoformat(history[0][0])
        last_ts = datetime.fromisoformat(history[-1][0])
        days_covered = max(0.0, (last_ts - first_ts).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        days_covered = 0.0

    slope_pct = 0.0
    if n >= 2 and days_covered > 0 and mean > 0:
        try:
            t0 = datetime.fromisoformat(history[0][0])
            xs = [(datetime.fromisoformat(ts) - t0).total_seconds() / 86400.0
                  for ts, _, _ in history]
            xm = statistics.fmean(xs)
            ym = mean
            num = sum((x - xm) * (y - ym) for x, y in zip(xs, prices))
            den = sum((x - xm) ** 2 for x in xs)
            if den > 0:
                slope_pct = (num / den) / mean * 100.0
        except (ValueError, TypeError):
            slope_pct = 0.0

    if n >= 4:
        head_n = 5 if n >= 10 else max(1, n // 2)
        tail_n = 5 if n >= 10 else max(1, n // 2)
        head_mean = statistics.fmean(prices[:head_n])
        tail_mean = statistics.fmean(prices[-tail_n:])
        recent_vs_old = (tail_mean / head_mean) if head_mean > 0 else 1.0
    else:
        recent_vs_old = 1.0

    return {
        "sample_count": n,
        "days_covered": round(days_covered, 2),
        "mean": round(mean, 4),
        "median": round(median, 4),
        "stdev": round(stdev, 4),
        "cv": round(cv, 4),
        "max_step_pct": round(max_step * 100.0, 2),
        "trend_slope_pct_per_day": round(slope_pct, 3),
        "recent_vs_old_ratio": round(recent_vs_old, 4),
    }


def _compute_source_agreement(
    price_sources: Optional[Dict[str, float]],
) -> Dict[str, object]:
    """Cross-source dispersion across the latest snapshot.

    Bands: tight (≤5%), loose (5-15%), wide (>15%). When <2 sources have
    usable values the band falls back to "unknown" and downstream rubrics
    treat it as a confidence dampener.
    """
    sources = price_sources or {}
    vals = [float(v) for v in sources.values() if v and float(v) > 0]
    if len(vals) < 2:
        return {
            "source_count": len(sources),
            "spread_pct": 0.0,
            "agreement_band": "unknown",
        }
    mean = statistics.fmean(vals)
    spread_pct = ((max(vals) - min(vals)) / mean * 100.0) if mean > 0 else 0.0
    if spread_pct <= 5.0:
        band = "tight"
    elif spread_pct <= 15.0:
        band = "loose"
    else:
        band = "wide"
    return {
        "source_count": len(sources),
        "spread_pct": round(spread_pct, 2),
        "agreement_band": band,
    }


def _rubric_confidence_ceiling(
    metrics: Dict[str, float],
    agreement: Dict[str, object],
) -> float:
    """Hard ceiling on per-horizon confidence given the computed signals.

    Matches the rubric in SYSTEM_PROMPT — kept here as the enforcement layer
    because the model occasionally over-claims even when told the rules.
    Worst-tier-wins: the lowest ceiling triggered fires.
    """
    n = metrics["sample_count"]
    cv = metrics["cv"]
    days = metrics["days_covered"]
    band = agreement["agreement_band"]
    if n < 5 or days < 3:
        return 0.2
    if n < 10 or cv >= 0.20 or band == "wide":
        return 0.3
    if n < 20 or cv > 0.08 or band == "unknown":
        return 0.7
    return 1.0


def _format_derived_signals(
    metrics: Dict[str, float],
    agreement: Dict[str, object],
    confidence_ceiling: float,
) -> str:
    """Render the derived metrics as a prompt block the model can quote."""
    cv = metrics["cv"]
    if cv <= 0.05:
        cv_desc = "very low"
    elif cv <= 0.10:
        cv_desc = "low"
    elif cv <= 0.20:
        cv_desc = "moderate"
    else:
        cv_desc = "high"
    slope = metrics["trend_slope_pct_per_day"]
    if slope > 0.10:
        trend_desc = "uptrend"
    elif slope < -0.10:
        trend_desc = "downtrend"
    else:
        trend_desc = "flat"
    band = agreement["agreement_band"]
    spread = agreement["spread_pct"]
    src_count = agreement["source_count"]
    return (
        "Derived signals (computed from the data above — these are facts, not "
        "your inferences; cite them by name in your reasoning):\n"
        f"  sample_count: {metrics['sample_count']}\n"
        f"  days_covered: {metrics['days_covered']:.2f}\n"
        f"  mean_price: ${metrics['mean']:.2f}    "
        f"median_price: ${metrics['median']:.2f}\n"
        f"  stdev: {metrics['stdev']:.3f}    "
        f"CV (coefficient of variation): {cv:.4f} ({cv_desc} volatility)\n"
        f"  max_single_step_swing: {metrics['max_step_pct']:.2f}%\n"
        f"  trend_slope: {slope:+.3f}%/day ({trend_desc})\n"
        f"  recent_vs_older_mean_ratio: {metrics['recent_vs_old_ratio']:.3f}\n"
        f"  source_agreement: {band} ({src_count} sources, spread {spread:.2f}%)\n"
        f"  rubric_confidence_ceiling: {confidence_ceiling:.2f}   "
        "(server will clamp your confidence to this max — stay below it)"
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
    metrics: Dict[str, float],
    agreement: Dict[str, object],
    confidence_ceiling: float,
) -> str:
    """Compose the per-item user prompt. History + derived signals + schema."""
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
    lines.append(_format_derived_signals(metrics, agreement, confidence_ceiling))
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
        '  "reasoning": "<2-3 sentences on what drives the projection — '
        'cite the derived signals by name>",\n'
        '  "drivers": ["<short bullet>", "<short bullet>"],\n'
        '  "caveats": ["<risks / why this could be wrong>"]\n'
        "}\n"
        "\n"
        "Rules:\n"
        "- All prices in USD.\n"
        "- low <= target <= high for each horizon.\n"
        "- Confidence MUST honor the rubric in the system prompt and the "
        "ceiling in the Derived signals block; the server will clamp anything "
        "over the ceiling.\n"
        "- Don't invent news. Only cite drivers you can infer from the history "
        "trend, the derived signals, the game/set/rarity context, or "
        "widely-known TCG dynamics."
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
    confidence_ceiling: float = 1.0,
) -> schemas.ForecastResult:
    """Parse the model's JSON into a strict ForecastResult.

    Sanitises: enforces low<=target<=high, clamps confidence to the rubric
    ceiling, normalises direction tag, caps reasoning at 600 chars.

    ``confidence_ceiling`` is computed from the derived metrics + source
    agreement upstream. The model is *told* this ceiling in the prompt
    but we enforce it here too — empirically the model still over-claims
    on thin data ~10% of the time, and an inflated confidence number in
    the UI is worse than a slightly muted-but-correct one.
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
            # Rubric clamp: hard ceiling per derived signals (the server's job,
            # not the model's — even when the model knows the rule it sometimes
            # over-claims on thin data).
            conf = min(conf, confidence_ceiling)
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
    serialised_history = _serialise_history(history)
    metrics = _compute_history_metrics(serialised_history)
    agreement = _compute_source_agreement(getattr(item, "price_sources", None))
    confidence_ceiling = _rubric_confidence_ceiling(metrics, agreement)

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
        history=serialised_history,
        metrics=metrics,
        agreement=agreement,
        confidence_ceiling=confidence_ceiling,
    )

    started = time.monotonic()
    try:
        ds_result = client.chat_json(SYSTEM_PROMPT, user_prompt, max_tokens=2500)
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
        confidence_ceiling=confidence_ceiling,
    )
    elapsed_ms = (time.monotonic() - started) * 1000.0
    logger.info(
        "forecast ok item=%s/%s horizons=%s ms=%.0f tokens_in=%s tokens_out=%s "
        "n=%s cv=%.3f band=%s ceiling=%.2f",
        item_type, item.id, len(result.horizons), elapsed_ms,
        ds_result.prompt_tokens, ds_result.completion_tokens,
        metrics["sample_count"], metrics["cv"],
        agreement["agreement_band"], confidence_ceiling,
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
