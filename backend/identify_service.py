"""Orchestration layer for the /identify endpoints.

Concerns split:
- ``providers/deepseek.py`` owns the HTTP transport to DeepSeek.
- This module owns the prompt, the JSON shape we expect back, the validation +
  sanitisation of model output (clamping confidence, normalising game tag,
  killing hallucinated URLs), and the batch fan-out semantics.

Why fan-out lives here, not in deepseek.py: parallelism is an app-level policy
(how aggressive can we be without tripping DeepSeek's rate limits?) rather than
a transport concern. Keeping it here also means tests can mock the DeepSeek
client without re-implementing the thread pool.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import schemas
from providers.deepseek import DeepSeekVision, DeepSeekVisionError

logger = logging.getLogger(__name__)

# Worker count for batch identify. Keeps DeepSeek call concurrency tame so we
# don't hit the published-or-not rate limit on a 50-image drop. Default 3 is
# conservative; bump via env when you've observed your account headroom.
DEFAULT_BATCH_WORKERS = int(os.environ.get("IDENTIFY_WORKERS", "3"))

# Hosts the LLM is allowed to cite in suggested_urls. Anything else is dropped
# — defends against the model inventing a malicious URL. The frontend uses
# these URLs by handing them to /catalog/resolve, which only knows these hosts
# anyway, so dropping foreign URLs has zero functional cost.
_ALLOWED_URL_HOSTS_RE = re.compile(
    r"^(www\.)?(tcgplayer\.com|scryfall\.com|api\.scryfall\.com|"
    r"pokemontcg\.io|api\.pokemontcg\.io|db\.ygoprodeck\.com|ygoprodeck\.com)$",
    re.IGNORECASE,
)

_VALID_GAMES = {"magic", "pokemon", "yugioh", "unknown"}

# Prompt template. Lives as a module constant so tests can assert it doesn't
# regress (e.g., accidentally dropping the JSON-only rule).
SYSTEM_PROMPT = (
    "You identify trading cards from photos for an inventory app. "
    "Supported games: Magic: The Gathering, Pokémon, Yu-Gi-Oh!. "
    "You ALWAYS respond with valid JSON matching the schema described in the "
    "user message — no prose, no markdown fences, no explanation. "
    "Conservative on confidence: 0.9+ means you can see the exact set + rarity, "
    "0.5-0.8 means you know the name but not the exact printing, <0.5 means "
    "you're guessing from partial visual cues. If you cannot identify a card, "
    'return {"candidates":[{"game":"unknown","name":"unidentified",'
    '"confidence":0.0,"justification":"<what you see>","suggested_urls":[],'
    '"search_queries":[]}]} rather than inventing a card.'
)

USER_PROMPT_TEMPLATE = """Identify the most prominent trading card in this image.

Return JSON matching this exact schema:

{{
  "candidates": [
    {{
      "game": "magic" | "pokemon" | "yugioh" | "unknown",
      "name": "<card name as printed>",
      "set_name": "<set/edition name or null>",
      "printing_notes": "<foil / holo / alt-art / starlight rare / etc or null>",
      "confidence": 0.0-1.0,
      "justification": "<one short sentence on what you observed>",
      "suggested_urls": ["https://www.tcgplayer.com/..."],
      "search_queries": ["dark magician starlight rare rarity collection"]
    }}
  ]
}}

Rules:
- Up to 3 candidates per image, sorted by confidence descending.
- Only include a TCGplayer URL when you can justify the exact printing
  (set + rarity visible). Otherwise leave suggested_urls=[].
- ALWAYS include at least one search_queries string (the same query you
  would type into TCGplayer's search box).
- NEVER invent TCGplayer product IDs — only cite a /product/<id>/<slug> URL
  if you can read it directly from packaging or a sticker in the image.
- If multiple cards visible in one image, pick the most prominent one only.
{game_hint_line}"""


def build_user_prompt(game_hint: Optional[str]) -> str:
    """Return the per-call user prompt with an optional game-hint line."""
    hint = ""
    if game_hint and game_hint.lower() in {"magic", "pokemon", "yugioh"}:
        hint = (
            f"- Caller hint: this image is probably {game_hint.upper()}. "
            f"Bias toward that game unless the visual evidence clearly "
            f"contradicts it."
        )
    return USER_PROMPT_TEMPLATE.format(game_hint_line=hint)


def _coerce_candidate(raw: dict) -> Optional[schemas.IdentifyCandidate]:
    """Validate + normalise one candidate dict from the model.

    Returns None when the candidate is unusable (no name AND no search query).
    Otherwise clamps confidence to [0, 1], normalises game tag, strips
    hallucinated URLs.
    """
    if not isinstance(raw, dict):
        return None

    game = str(raw.get("game") or "").strip().lower()
    if game not in _VALID_GAMES:
        game = "unknown"

    name = str(raw.get("name") or "").strip()
    confidence = raw.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    raw_urls = raw.get("suggested_urls") or []
    if not isinstance(raw_urls, list):
        raw_urls = []
    suggested_urls: List[str] = []
    for url in raw_urls:
        if not isinstance(url, str):
            continue
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            continue
        if _ALLOWED_URL_HOSTS_RE.match(host):
            suggested_urls.append(url)
        else:
            logger.info("dropping hallucinated url host=%s", host)

    raw_queries = raw.get("search_queries") or []
    if not isinstance(raw_queries, list):
        raw_queries = []
    search_queries = [
        str(q).strip() for q in raw_queries if isinstance(q, str) and q.strip()
    ]

    # Useful candidate must have at least a name OR a search query. An empty
    # candidate from the model is dropped here so the frontend doesn't render
    # a blank card.
    if not name and not search_queries:
        return None

    return schemas.IdentifyCandidate(
        game=game,
        name=name or "unidentified",
        set_name=(raw.get("set_name") or None) or None,
        printing_notes=(raw.get("printing_notes") or None) or None,
        confidence=confidence,
        justification=str(raw.get("justification") or "").strip(),
        suggested_urls=suggested_urls,
        search_queries=search_queries,
    )


def _parse_model_output(raw_content: str) -> List[schemas.IdentifyCandidate]:
    """Parse + sanitise the JSON string DeepSeek returned.

    Raises ValueError when the JSON is unparseable or doesn't contain a
    ``candidates`` list (caught upstream and surfaced as a per-image error).
    """
    try:
        body = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model returned non-JSON: {exc}") from exc

    if not isinstance(body, dict):
        raise ValueError("Model JSON is not an object")
    raw_candidates = body.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("Model JSON missing 'candidates' list")

    out: List[schemas.IdentifyCandidate] = []
    for raw in raw_candidates[:3]:  # cap at 3 even if model returns more
        candidate = _coerce_candidate(raw)
        if candidate is not None:
            out.append(candidate)
    # Sort by confidence descending — the model is asked to do this but we
    # enforce it defensively.
    out.sort(key=lambda c: -c.confidence)
    return out


def identify_single(
    client: DeepSeekVision,
    filename: str,
    image_bytes: bytes,
    mime_type: str,
    game_hint: Optional[str] = None,
) -> schemas.IdentifyResult:
    """Identify one image. Always returns an IdentifyResult — never raises."""
    started = time.monotonic()
    try:
        result = client.identify(
            images=[(image_bytes, mime_type)],
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(game_hint),
        )
    except DeepSeekVisionError as exc:
        logger.warning("identify failed file=%s: %s", filename, exc)
        return schemas.IdentifyResult(
            source_filename=filename, candidates=[], error=str(exc)
        )

    try:
        candidates = _parse_model_output(result.raw_content)
    except ValueError as exc:
        logger.warning(
            "identify parse failed file=%s content=%s: %s",
            filename, result.raw_content[:200], exc,
        )
        return schemas.IdentifyResult(
            source_filename=filename, candidates=[],
            error=f"Model output unparseable: {exc}",
        )

    elapsed_ms = (time.monotonic() - started) * 1000.0
    logger.info(
        "identify ok file=%s candidates=%s ms=%.0f tokens_in=%s tokens_out=%s",
        filename, len(candidates), elapsed_ms,
        result.prompt_tokens, result.completion_tokens,
    )
    return schemas.IdentifyResult(
        source_filename=filename, candidates=candidates, error=None
    )


def identify_batch(
    client: DeepSeekVision,
    items: Iterable[Tuple[str, bytes, str]],
    *,
    max_workers: int = DEFAULT_BATCH_WORKERS,
) -> schemas.IdentifyBatchResponse:
    """Identify many images concurrently. One item failing doesn't kill others.

    ``items`` is an iterable of (filename, image_bytes, mime_type) tuples.
    """
    items_list = list(items)
    if not items_list:
        return schemas.IdentifyBatchResponse(results=[], duration_seconds=0.0)

    started = time.monotonic()

    def _run(item: Tuple[str, bytes, str]) -> schemas.IdentifyResult:
        filename, body, mime = item
        return identify_single(client, filename, body, mime)

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        results = list(pool.map(_run, items_list))

    duration = time.monotonic() - started
    logger.info(
        "identify batch n=%s ok=%s err=%s duration_s=%.2f",
        len(results),
        sum(1 for r in results if not r.error),
        sum(1 for r in results if r.error),
        duration,
    )
    return schemas.IdentifyBatchResponse(
        results=results, duration_seconds=round(duration, 2)
    )
