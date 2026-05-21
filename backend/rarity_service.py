"""Rarity decision layer — the judgment half of the hybrid rarity engine.

``rarity_cv.py`` produces deterministic measurements; this module feeds those
numbers plus the sharpest frames to DeepSeek with an explicit rarity rubric and
gets back a single rarity label + confidence. It also owns:

- the canonical-vocab SNAP (the model's answer is forced onto a known rarity,
  so a hallucinated "Mega Ultra Rare" can't reach the price lookup),
- the CV-only RULE fallback (when DeepSeek is unconfigured/down we still make a
  best guess from the measurements at low confidence),
- ``method`` bookkeeping so the UI / future auto-add knows how we decided.

Contract: ``detect_rarity`` NEVER raises — every failure path returns a
RarityResult (possibly low-confidence "unknown") so the scan endpoint can't 500.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
from typing import List, Optional, Tuple

import schemas
from providers.deepseek import DeepSeekVision, DeepSeekVisionError

logger = logging.getLogger(__name__)

Frame = Tuple[bytes, str]

# Send at most this many frames to the model — enough angle variety to read the
# foil without blowing the token budget.
_MAX_RARITY_FRAMES = 3

# Foil-line angle bands (degrees, folded to [0,90]).
_ORTHOGONAL_MAX = 20.0   # ≤ this (or ≥ 90-this) reads as horizontal/vertical
_DIAGONAL_LO, _DIAGONAL_HI = 30.0, 60.0

# CV-only rule thresholds (intentionally loose; tuned later with fixtures).
_STRONG_LINE = 2.5          # foil_line_strength above this = a real line pattern
_HIGH_MOTION = 0.20         # sparkle_motion above this = foil dances under tilt
_HIGH_VARNISH = 1.6         # frame texturier than art = raised varnish
_HIGH_DAZZLE = 25.0         # textured black border


RARITY_SYSTEM = (
    "You are a Yu-Gi-Oh! card RARITY grader for an inventory app. You are given "
    "frames from a short video where the card is tilted under light, plus "
    "computer-vision measurements of the foil. Decide the single most likely "
    "rarity. You ALWAYS respond with valid JSON only — no prose, no markdown "
    "fences. You MUST choose the rarity from the provided allowed list; never "
    "invent a rarity. Be conservative: if the foil cues are ambiguous, lower "
    "your confidence and list the plausible options in 'alternatives'."
)

# The visual rubric — the four Rarity Collection rarities have distinctive,
# motion-revealed tells. Kept as a module constant so tests assert it doesn't
# silently lose a cue.
RARITY_RUBRIC = (
    "Rarity tells (especially for Rarity Collection cards, where the SAME card "
    "prints in many rarities):\n"
    "- Quarter Century Secret Rare: HORIZONTAL/VERTICAL (orthogonal) see-through "
    "foil lines across the whole card; the card NAME is champagne-GOLD; a '25' "
    "anniversary watermark is embossed in the text box.\n"
    "- Platinum Secret Rare: DIAGONAL see-through foil lines across the whole "
    "card; the card name has a SHATTERED-GLASS texture (not gold).\n"
    "- Prismatic Ultimate Rare: physical RAISED varnish texture on the colored "
    "frame; glossy black border; 3-D varnish gloss on the art highlights.\n"
    "- Prismatic Collector's Rare: glossy varnish on the colored frame; a DAZZLE "
    "pattern on the black border; pixellated sparkles; textured art.\n"
    "- Secret Rare: diagonal foil on the NAME only (not the whole card).\n"
    "- Ultra Rare: gold foil name, holo art, no see-through lines.\n"
    "- Super Rare: holo art only, plain (silver) name.\n"
    "- Rare: subtle silver-foil name, non-holo art. Common: no foil."
)


def _measurements_text(m: Optional[schemas.RarityMeasurements]) -> str:
    """Render the CV measurements as a compact hint block for the prompt."""
    if m is None:
        return "Computer-vision measurements: (none available)."
    parts: List[str] = []
    if m.foil_line_angle_deg is not None:
        orient = _orientation_label(m.foil_line_angle_deg)
        parts.append(
            f"dominant foil-line angle ≈ {m.foil_line_angle_deg:.0f}° ({orient}), "
            f"strength {m.foil_line_strength:.1f}"
            if m.foil_line_strength is not None
            else f"dominant foil-line angle ≈ {m.foil_line_angle_deg:.0f}° ({orient})"
        )
    if m.sparkle_density is not None:
        parts.append(f"sparkle density {m.sparkle_density:.3f}")
    if m.sparkle_motion is not None:
        parts.append(f"sparkle motion under tilt {m.sparkle_motion:.2f}")
    if m.name_is_champagne_gold is not None:
        parts.append(
            f"card-name colour {'IS' if m.name_is_champagne_gold else 'is NOT'} "
            f"champagne-gold"
            + (f" (hue {m.name_region_hue:.0f}°)" if m.name_region_hue is not None else "")
        )
    if m.frame_varnish_energy is not None:
        parts.append(f"frame-vs-art varnish energy {m.frame_varnish_energy:.2f}")
    if m.border_dazzle_score is not None:
        parts.append(f"black-border dazzle score {m.border_dazzle_score:.0f}")
    if not parts:
        return "Computer-vision measurements: (extraction produced nothing usable)."
    rect = "card was rectified" if m.rectified else "card could NOT be rectified (measures approximate)"
    return "Computer-vision measurements (" + rect + "): " + "; ".join(parts) + "."


def _orientation_label(angle: float) -> str:
    if angle <= _ORTHOGONAL_MAX or angle >= (90.0 - _ORTHOGONAL_MAX):
        return "orthogonal: horizontal/vertical"
    if _DIAGONAL_LO <= angle <= _DIAGONAL_HI:
        return "diagonal"
    return "oblique"


def _build_user_prompt(
    name: str, set_name: Optional[str], m: Optional[schemas.RarityMeasurements]
) -> str:
    allowed = ", ".join(schemas.RARITY_VOCAB)
    return (
        f"Card: {name}\n"
        f"Set (if known): {set_name or 'unknown'}\n\n"
        f"{RARITY_RUBRIC}\n\n"
        f"{_measurements_text(m)}\n\n"
        f"Allowed rarities (choose EXACTLY one for 'rarity', and 0+ for "
        f"'alternatives', all from this list): {allowed}\n\n"
        "Return JSON of this exact shape:\n"
        '{"rarity": "<one allowed rarity>", "confidence": 0.0-1.0, '
        '"alternatives": ["<other allowed rarities you considered>"], '
        '"reasoning": "<one short sentence citing the cues you used>"}'
    )


def _norm(text: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


_VOCAB_NORM = {_norm(v): v for v in schemas.RARITY_VOCAB}


def snap_to_vocab(rarity: Optional[str]) -> Optional[str]:
    """Force a model-returned rarity onto the canonical vocab.

    Exact normalized match first, then closest fuzzy match. Returns None when
    nothing is close enough (caller treats that as 'unknown')."""
    n = _norm(rarity)
    if not n:
        return None
    if n in _VOCAB_NORM:
        return _VOCAB_NORM[n]
    close = difflib.get_close_matches(n, list(_VOCAB_NORM.keys()), n=1, cutoff=0.82)
    return _VOCAB_NORM[close[0]] if close else None


def _filter_alternatives(raw, chosen: str) -> List[str]:
    out: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            snapped = snap_to_vocab(item if isinstance(item, str) else None)
            if snapped and snapped != chosen and snapped not in out:
                out.append(snapped)
    return out


def rule_based_guess(
    m: Optional[schemas.RarityMeasurements],
) -> Tuple[Optional[str], List[str], str]:
    """Best-effort rarity from CV measurements alone (DeepSeek-down fallback).

    Returns (rarity_or_None, alternatives, reasoning). Deliberately low-stakes:
    the caller stamps a low confidence on whatever this returns.
    """
    if m is None:
        return None, [], "no measurements"

    angle = m.foil_line_angle_deg
    strong = (m.foil_line_strength or 0.0) >= _STRONG_LINE
    orthogonal = angle is not None and (angle <= _ORTHOGONAL_MAX or angle >= 90.0 - _ORTHOGONAL_MAX)
    diagonal = angle is not None and (_DIAGONAL_LO <= angle <= _DIAGONAL_HI)

    if strong and orthogonal and m.name_is_champagne_gold:
        return ("Quarter Century Secret Rare",
                ["Platinum Secret Rare"],
                "orthogonal see-through lines + champagne-gold name")
    if strong and diagonal:
        return ("Platinum Secret Rare",
                ["Quarter Century Secret Rare"],
                "diagonal see-through foil lines across the card")
    if (m.frame_varnish_energy or 0.0) >= _HIGH_VARNISH:
        return ("Prismatic Ultimate Rare",
                ["Prismatic Collector's Rare"],
                "raised varnish texture on the colored frame")
    if (m.border_dazzle_score or 0.0) >= _HIGH_DAZZLE:
        return ("Prismatic Collector's Rare",
                ["Prismatic Ultimate Rare"],
                "dazzle pattern on the black border")
    if (m.sparkle_motion or 0.0) >= _HIGH_MOTION:
        return ("Secret Rare", ["Ultra Rare"], "holo foil that shifts under tilt")
    if (m.sparkle_density or 0.0) > 0.02:
        return ("Super Rare", ["Ultra Rare"], "holo art, no see-through lines")
    return ("Common", ["Rare"], "no foil signal detected")


def _has_signal(m: Optional[schemas.RarityMeasurements]) -> bool:
    if m is None:
        return False
    return any(v is not None for v in (
        m.foil_line_angle_deg, m.sparkle_density, m.sparkle_motion,
        m.name_is_champagne_gold, m.frame_varnish_energy, m.border_dazzle_score,
    ))


def detect_rarity(
    frames: List[Frame],
    name: str,
    set_name: Optional[str] = None,
    *,
    client: Optional[DeepSeekVision] = None,
    measurements: Optional[schemas.RarityMeasurements] = None,
) -> schemas.RarityResult:
    """Decide the rarity. Hybrid when measurements + a configured client exist;
    vision-only without measurements; cv-only rule fallback when the client is
    unavailable. Never raises."""
    has_cv = _has_signal(measurements)

    # No usable model → CV rule fallback (or unknown when we have nothing).
    if client is None or not client.is_configured():
        rarity, alts, why = rule_based_guess(measurements)
        if rarity is None:
            return schemas.RarityResult(
                rarity="unknown", confidence=0.0, method="unknown",
                reasoning="No DeepSeek key and no CV measurements to fall back on.",
                measurements=measurements,
            )
        return schemas.RarityResult(
            rarity=rarity, confidence=0.4, alternatives=alts,
            reasoning=f"CV-only (DeepSeek unavailable): {why}.",
            method="cv_only", measurements=measurements,
        )

    try:
        result = client.identify(
            images=frames[:_MAX_RARITY_FRAMES],
            system_prompt=RARITY_SYSTEM,
            user_prompt=_build_user_prompt(name, set_name, measurements),
            max_tokens=600,
            temperature=0.1,
        )
        data = json.loads(result.raw_content)
        if not isinstance(data, dict):
            raise ValueError("rarity JSON is not an object")
    except (DeepSeekVisionError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("detect_rarity model path failed (%s) — CV fallback", exc)
        rarity, alts, why = rule_based_guess(measurements)
        if rarity is None:
            return schemas.RarityResult(
                rarity="unknown", confidence=0.0, method="unknown",
                reasoning=f"DeepSeek failed ({exc}) and no CV fallback available.",
                measurements=measurements,
            )
        return schemas.RarityResult(
            rarity=rarity, confidence=0.35, alternatives=alts,
            reasoning=f"DeepSeek failed; CV-only: {why}.",
            method="cv_only", measurements=measurements,
        )

    chosen = snap_to_vocab(data.get("rarity"))
    if chosen is None:
        # Model returned something off-vocab — distrust it, fall back to CV.
        rarity, alts, why = rule_based_guess(measurements)
        if rarity is not None:
            return schemas.RarityResult(
                rarity=rarity, confidence=0.35, alternatives=alts,
                reasoning=f"Model rarity off-vocab; CV-only: {why}.",
                method="cv_only", measurements=measurements,
            )
        return schemas.RarityResult(
            rarity="unknown", confidence=0.0, method="unknown",
            reasoning="Model returned an unrecognized rarity.",
            measurements=measurements,
        )

    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0

    logger.info(
        "detect_rarity name=%r chose=%s conf=%.2f method=%s has_cv=%s",
        name, chosen, confidence, "hybrid" if has_cv else "vision_only", has_cv,
    )
    return schemas.RarityResult(
        rarity=chosen,
        confidence=confidence,
        alternatives=_filter_alternatives(data.get("alternatives"), chosen),
        reasoning=str(data.get("reasoning") or "").strip(),
        method="hybrid" if has_cv else "vision_only",
        measurements=measurements,
    )
