"""DeepSeek multimodal client — image identification for the /identify endpoints.

Why this module exists:
- The catalog providers (`tcgplayer.py`, `ebay.py`, `cardmarket.py`) all fetch
  PRICES. DeepSeek doesn't price; it identifies the card from pixels and hands
  back a TCGplayer URL the existing resolver can chew. Distinct concern, lives
  outside the price-provider Protocol.
- We talk to DeepSeek over OpenAI-compatible REST (their stated wire format).
- Image input is the OpenAI ``image_url`` content-block shape with a base64
  data URI. No multipart upload to DeepSeek — that's only between our frontend
  and our backend.
- Hosted-API model defaults to ``deepseek-v4-pro`` (best multimodal accuracy
  per DeepSeek's marketing). Override with ``DEEPSEEK_MODEL`` env var.
- Video is NOT supported by the hosted API. The video endpoint extracts frames
  with ffmpeg and calls into this client with N images. (See Phase 3.)
"""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .base import request_with_backoff

logger = logging.getLogger(__name__)

DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-pro"

# Conservative per-image cap. DeepSeek doesn't publish a hard limit; we cap
# at 10 MB before base64-encoding so an oversized phone photo doesn't blow up
# the JSON payload. Frontend should resize before upload; backend enforces.
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Conservative timeout — vision models can take 10-30s on tricky images. The
# backoff helper will retry on 5xx/429 with this base timeout per attempt.
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class DeepSeekResult:
    """Raw JSON the model returned plus the bookkeeping the service layer needs.

    ``raw_content`` is the unparsed JSON string from the model — the service
    layer is responsible for json.loads + Pydantic validation. Keeping that
    layer separate makes mocking trivial in tests.
    """
    raw_content: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class DeepSeekVisionError(Exception):
    """Raised when DeepSeek returns a non-2xx or an unparseable response.

    Caught in identify_service and surfaced to the API caller as an `error`
    field on the IdentifyResult, NOT as a 500 — per-image failures isolate.
    """


class DeepSeekVision:
    """Thin OpenAI-compatible client scoped to one Pi instance / one API key."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # Read env at construction so tests can monkeypatch os.environ before
        # instantiating. Don't cache the key at module import; the user may set
        # it after the service has already loaded (lifespan-friendly).
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
        self.timeout = timeout

    def is_configured(self) -> bool:
        """True iff we have an API key. Used by the endpoint to 503 cleanly."""
        return bool(self.api_key)

    def identify(
        self,
        images: List[Tuple[bytes, str]],
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 2000,
        temperature: float = 0.2,
    ) -> DeepSeekResult:
        """Send one or more images plus a text prompt; return raw JSON content.

        ``images`` is a list of (bytes, mime_type) tuples. All images are bundled
        into a single user message — DeepSeek (per the OpenAI shape) supports
        multiple image_url blocks in one content array. For the video endpoint
        this is how we send all extracted frames in one round trip.

        Raises DeepSeekVisionError on:
        - missing API key
        - oversized image (> MAX_IMAGE_BYTES)
        - non-2xx response from DeepSeek
        - response missing the expected ``choices[0].message.content`` shape
        """
        if not self.api_key:
            raise DeepSeekVisionError(
                "DEEPSEEK_API_KEY env var not set. "
                "Identification is disabled until a key is provided."
            )
        if not images:
            raise DeepSeekVisionError("identify() called with zero images")

        # Build OpenAI-compatible content array: text first, then images.
        content: List[Dict] = [{"type": "text", "text": user_prompt}]
        for idx, (data, mime) in enumerate(images):
            if len(data) > MAX_IMAGE_BYTES:
                raise DeepSeekVisionError(
                    f"Image {idx} is {len(data)} bytes "
                    f"(cap is {MAX_IMAGE_BYTES} bytes; resize before upload)"
                )
            if not mime.startswith("image/"):
                raise DeepSeekVisionError(
                    f"Image {idx} has non-image mime type {mime!r}"
                )
            b64 = base64.b64encode(data).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        logger.info(
            "deepseek identify request model=%s images=%s prompt_chars=%s",
            self.model, len(images), len(user_prompt),
        )
        # NOTE: we deliberately do NOT log payload contents (would include
        # base64 image bytes — both useless in logs and a privacy leak).

        resp = request_with_backoff(
            "POST",
            DEEPSEEK_CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if resp is None:
            raise DeepSeekVisionError("DeepSeek API unreachable after retries")
        if resp.status_code >= 400:
            # Don't echo the API's response body in user-facing errors — could
            # contain prompts that leak our prompt template to a malicious caller.
            logger.error(
                "deepseek %s response: %s", resp.status_code, resp.text[:500]
            )
            raise DeepSeekVisionError(
                f"DeepSeek returned HTTP {resp.status_code}"
            )

        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise DeepSeekVisionError(
                f"DeepSeek response not JSON: {exc}"
            ) from exc

        try:
            choice = body["choices"][0]
            raw_content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DeepSeekVisionError(
                f"DeepSeek response missing choices[0].message.content: {exc}"
            ) from exc

        usage = body.get("usage") or {}
        return DeepSeekResult(
            raw_content=raw_content,
            model=body.get("model") or self.model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> DeepSeekResult:
        """Text-only chat with strict JSON output mode.

        Used by the forecast endpoint (no images, just card data → price
        projection JSON). Shares auth + endpoint + retry plumbing with
        ``identify()``. Same DeepSeekVisionError on failure so callers handle
        one exception type for both code paths.
        """
        if not self.api_key:
            raise DeepSeekVisionError(
                "DEEPSEEK_API_KEY env var not set."
            )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        logger.info(
            "deepseek chat_json request model=%s prompt_chars=%s",
            self.model, len(user_prompt),
        )
        resp = request_with_backoff(
            "POST",
            DEEPSEEK_CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if resp is None:
            raise DeepSeekVisionError("DeepSeek API unreachable after retries")
        if resp.status_code >= 400:
            logger.error(
                "deepseek chat_json %s response: %s",
                resp.status_code, resp.text[:500],
            )
            raise DeepSeekVisionError(
                f"DeepSeek returned HTTP {resp.status_code}"
            )
        try:
            body = resp.json()
            raw_content = body["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise DeepSeekVisionError(
                f"DeepSeek chat_json response malformed: {exc}"
            ) from exc
        usage = body.get("usage") or {}
        return DeepSeekResult(
            raw_content=raw_content,
            model=body.get("model") or self.model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )
