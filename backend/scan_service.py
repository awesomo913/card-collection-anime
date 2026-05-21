"""Orchestrator for the /scan card scanner.

Wires the pieces together for one scan:

    capture burst (camera_service)
      → identify card name/set (identify_service over the frames)
        → measure foil (rarity_cv)
          → decide rarity (rarity_service)
            → price that exact rarity printing (providers.catalog)
              → assemble a CardCreate-shaped `ready_to_add`

It does NOT commit — the frontend reviews the result and POSTs `ready_to_add`
to the EXISTING /cards/ endpoint to add it (so dedup + price-fetch + history
all stay in one place).

Every collaborator is injectable so the whole flow is unit-testable headless
(fake camera + mocked DeepSeek). Capture failure (no camera) propagates as
CameraUnavailableError for the endpoint to turn into a 503; every other
per-stage failure is captured on ScanResult.error — run_scan otherwise never
raises.
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional, Tuple

import camera_service
import identify_service
import rarity_cv
import rarity_service
import schemas
from providers import catalog as catalog_module
from providers.deepseek import DeepSeekVision

logger = logging.getLogger(__name__)

Frame = Tuple[bytes, str]

# Rarities that are NOT foil — everything else gets is_foil=True so the price
# lookup + dedup treat it as the foil printing.
_NON_FOIL_RARITIES = {"common", "rare"}

_DEFAULT_CONDITION = "Near Mint"


def _is_foil(rarity: Optional[str]) -> bool:
    return (rarity or "").strip().lower() not in _NON_FOIL_RARITIES


def _pick_candidate(
    result: schemas.IdentifyResult, game_hint: Optional[str]
) -> Optional[schemas.IdentifyCandidate]:
    """Highest-confidence usable candidate, preferring the hinted game."""
    usable = [c for c in result.candidates if c.name and c.name != "unidentified"]
    if not usable:
        return None
    if game_hint:
        hinted = [c for c in usable if c.game == game_hint]
        if hinted:
            return max(hinted, key=lambda c: c.confidence)
    return max(usable, key=lambda c: c.confidence)


def run_scan(
    client: DeepSeekVision,
    *,
    game_hint: Optional[str] = "yugioh",
    capture: Optional[Callable[[], List[Frame]]] = None,
    identifier: Optional[Callable] = None,
    measurer: Optional[Callable] = None,
    rarity_detector: Optional[Callable] = None,
    pricer: Optional[Callable] = None,
) -> schemas.ScanResult:
    """Run one full scan. See module docstring. Raises CameraUnavailableError
    only when capture has no camera/fixtures; all else lands on .error."""
    capture = capture or camera_service.capture_burst
    identifier = identifier or identify_service.identify_frames
    measurer = measurer or rarity_cv.measure
    rarity_detector = rarity_detector or rarity_service.detect_rarity
    pricer = pricer or catalog_module.lookup_yugioh_by_rarity

    # 1. Capture — CameraUnavailableError propagates to the 503 gate.
    frames = capture()
    n = len(frames)

    # 2. Identify the card.
    ident = identifier(client, "scan", frames, game_hint)
    candidate = _pick_candidate(ident, game_hint)
    if candidate is None:
        return schemas.ScanResult(
            frames_captured=n,
            error=ident.error or "Could not identify a card in the captured frames.",
        )
    scan_candidate = schemas.ScanCandidate(
        game=candidate.game, name=candidate.name,
        set_name=candidate.set_name, confidence=candidate.confidence,
    )

    # 3. Measure foil (deterministic, never raises) + 4. decide rarity.
    measurements = measurer(frames)
    rarity = rarity_detector(
        frames, candidate.name, candidate.set_name,
        client=client, measurements=measurements,
    )

    # 5. Price the exact rarity printing (Yu-Gi-Oh only; other games skip).
    price = schemas.ScanPrice()
    resolved_set = candidate.set_name
    external_id: Optional[str] = None
    image_url: Optional[str] = None
    set_code: Optional[str] = None
    price_error: Optional[str] = None
    if candidate.game == "yugioh":
        try:
            hit = pricer(
                name=candidate.name,
                set_name=candidate.set_name,
                rarity=rarity.rarity if rarity else None,
            )
        except Exception as exc:  # noqa: BLE001 — pricing must never sink the scan
            logger.warning("scan price lookup failed name=%r: %s", candidate.name, exc)
            hit = None
            price_error = f"price lookup failed: {exc}"
        if hit:
            price = schemas.ScanPrice(
                tcgplayer_price=hit.get("tcgplayer_price"),
                source=hit.get("price_source"),
            )
            resolved_set = hit.get("set_name") or resolved_set
            external_id = hit.get("external_id") or None
            image_url = hit.get("image_url")
            set_code = hit.get("set_code")
        else:
            price_error = price_error or "Card not found in the Yu-Gi-Oh catalog."

    # 6. Assemble the ready-to-commit payload (frontend POSTs to /cards/).
    rarity_label = rarity.rarity if rarity and rarity.rarity != "unknown" else None
    ready = schemas.CardCreate(
        name=candidate.name,
        set_name=resolved_set or "",
        card_number=set_code,
        rarity=rarity_label,
        condition=_DEFAULT_CONDITION,
        quantity=1,
        is_foil=_is_foil(rarity_label),
        game=candidate.game or "yugioh",
        external_source="ygoprodeck" if external_id else None,
        external_id=external_id,
        image_url=image_url,
    )

    logger.info(
        "scan done name=%r set=%r rarity=%s conf=%.2f price=%s frames=%s",
        candidate.name, resolved_set, rarity_label,
        rarity.confidence if rarity else 0.0, price.tcgplayer_price, n,
    )
    return schemas.ScanResult(
        candidate=scan_candidate,
        rarity=rarity,
        price=price,
        ready_to_add=ready,
        frames_captured=n,
        error=price_error,
    )


def reprice(req: schemas.ScanRepriceRequest) -> schemas.ScanRepriceResponse:
    """Re-price for a user-overridden rarity (no camera / no DeepSeek needed)."""
    try:
        hit = catalog_module.lookup_yugioh_by_rarity(
            card_id=req.external_id, name=req.name,
            set_name=req.set_name, rarity=req.rarity,
        )
    except Exception as exc:  # noqa: BLE001 — a price lookup must never 500 the UI
        logger.warning("reprice lookup failed id=%r rarity=%r: %s",
                       req.external_id, req.rarity, exc)
        return schemas.ScanRepriceResponse(source="error")
    if not hit:
        return schemas.ScanRepriceResponse()
    return schemas.ScanRepriceResponse(
        tcgplayer_price=hit.get("tcgplayer_price"),
        tcgplayer_product_id=hit.get("tcgplayer_product_id"),
        set_name=hit.get("set_name"),
        source=hit.get("price_source"),
    )
