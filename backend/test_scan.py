"""Tests for the /scan card scanner: rarity-aware pricing, the rarity decision
layer, the scan orchestrator, and the endpoint wiring.

All offline — no camera, no network, no DeepSeek. The camera is faked or the
collaborators are injected; DeepSeek is a stub returning canned JSON.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))

import schemas  # noqa: E402


# ---------------------------------------------------------------------------
# Rarity-aware pricing (providers/catalog.py)
# ---------------------------------------------------------------------------

# One Rarity Collection card printed in 8 rarities — same set name, different
# set_rarity + set_price. The OLD picker (set-name only) would return a random
# one of these; the rarity-aware picker must select by rarity.
_EIGHT_RARITY_SETS = [
    {"set_name": "Rarity Collection", "set_code": "RA04-EN001", "set_rarity": "Super Rare", "set_price": "0.50"},
    {"set_name": "Rarity Collection", "set_code": "RA04-EN001", "set_rarity": "Ultra Rare", "set_price": "1.20"},
    {"set_name": "Rarity Collection", "set_code": "RA04-EN001", "set_rarity": "Secret Rare", "set_price": "3.00"},
    {"set_name": "Rarity Collection", "set_code": "RA04-EN001", "set_rarity": "Ultimate Rare", "set_price": "5.00"},
    {"set_name": "Rarity Collection", "set_code": "RA04-EN001", "set_rarity": "Collector's Rare", "set_price": "8.00"},
    {"set_name": "Rarity Collection", "set_code": "RA04-EN001", "set_rarity": "Quarter Century Secret Rare", "set_price": "15.00"},
    {"set_name": "Rarity Collection", "set_code": "RA04-EN001", "set_rarity": "Platinum Secret Rare", "set_price": "40.00"},
    {"set_name": "Rarity Collection", "set_code": "RA04-EN001", "set_rarity": "Prismatic Collector's Rare", "set_price": "60.00"},
]


def test_pick_printing_selects_each_rarity_own_price():
    from providers import catalog
    for entry in _EIGHT_RARITY_SETS:
        chosen = catalog._pick_yugioh_printing_by_rarity(
            _EIGHT_RARITY_SETS, ["rarity", "collection"], entry["set_rarity"]
        )
        assert chosen["set_rarity"] == entry["set_rarity"]
        assert chosen["set_price"] == entry["set_price"]


def test_pick_printing_quarter_century_vs_platinum_differ():
    """Headline: two rarities of the SAME card resolve to different prices."""
    from providers import catalog
    qc = catalog._pick_yugioh_printing_by_rarity(
        _EIGHT_RARITY_SETS, ["rarity", "collection"], "Quarter Century Secret Rare"
    )
    plat = catalog._pick_yugioh_printing_by_rarity(
        _EIGHT_RARITY_SETS, ["rarity", "collection"], "Platinum Secret Rare"
    )
    assert qc["set_price"] != plat["set_price"]
    assert qc["set_price"] == "15.00"
    assert plat["set_price"] == "40.00"


def test_pick_printing_unmatched_rarity_falls_back():
    """A rarity that matches no printing falls back to the name-based pick
    rather than returning a wrong-rarity price out of nowhere."""
    from providers import catalog
    chosen = catalog._pick_yugioh_printing_by_rarity(
        _EIGHT_RARITY_SETS, ["rarity", "collection"], "Ghost Rare"
    )
    assert chosen is not None  # falls back, never None when sets exist


def test_lookup_yugioh_by_rarity_prices_the_rarity(monkeypatch):
    from providers import catalog
    fake_card = {
        "id": 12345,
        "name": "Crossout Designator",
        "card_images": [{"image_url_small": "http://img/small.jpg"}],
        "card_prices": [{"tcgplayer_price": "2.00"}],
        "card_sets": _EIGHT_RARITY_SETS,
    }
    monkeypatch.setattr(catalog, "_fetch_ygoprodeck_card", lambda **k: fake_card)

    plat = catalog.lookup_yugioh_by_rarity(
        name="Crossout Designator", set_name="Rarity Collection",
        rarity="Platinum Secret Rare",
    )
    assert plat["tcgplayer_price"] == 40.0
    assert plat["set_code"] == "RA04-EN001"
    assert plat["rarity"] == "Platinum Secret Rare"
    assert plat["external_id"] == "12345"

    qc = catalog.lookup_yugioh_by_rarity(
        name="Crossout Designator", set_name="Rarity Collection",
        rarity="Quarter Century Secret Rare",
    )
    assert qc["tcgplayer_price"] == 15.0  # different rarity → different price


def test_lookup_matched_rarity_with_zero_price_does_not_show_aggregate(monkeypatch):
    """A rarity that matches but has set_price '0' must NOT silently fall back to
    the card-wide aggregate (a different rarity's price). It reports no price."""
    from providers import catalog
    fake_card = {
        "id": 7, "name": "Some Card",
        "card_images": [{"image_url_small": "http://img.jpg"}],
        "card_prices": [{"tcgplayer_price": "2.00"}],  # aggregate = a trap here
        "card_sets": [
            {"set_name": "Rarity Collection", "set_code": "RA04-EN050",
             "set_rarity": "Platinum Secret Rare", "set_price": "0"},
        ],
    }
    monkeypatch.setattr(catalog, "_fetch_ygoprodeck_card", lambda **k: fake_card)
    hit = catalog.lookup_yugioh_by_rarity(
        name="Some Card", set_name="Rarity Collection", rarity="Platinum Secret Rare",
    )
    assert hit["tcgplayer_price"] is None
    assert hit["price_source"] == "ygoprodeck:no_price_for_rarity"
    assert hit["tcgplayer_price"] != 2.0  # never the wrong-rarity aggregate


# ---------------------------------------------------------------------------
# Rarity decision layer (rarity_service.py)
# ---------------------------------------------------------------------------

import rarity_service  # noqa: E402
from providers.deepseek import DeepSeekResult, DeepSeekVisionError  # noqa: E402


class StubClient:
    """Stand-in for DeepSeekVision in rarity tests."""

    def __init__(self, raw=None, raise_exc=None, configured=True):
        self._raw = raw
        self._raise = raise_exc
        self._configured = configured

    def is_configured(self):
        return self._configured

    def identify(self, images, system_prompt, user_prompt, **kw):
        if self._raise:
            raise self._raise
        return DeepSeekResult(raw_content=self._raw, model="stub",
                              prompt_tokens=0, completion_tokens=0)


def test_snap_to_vocab():
    assert rarity_service.snap_to_vocab("Quarter Century Secret Rare") == "Quarter Century Secret Rare"
    # Punctuation/spacing drift snaps to canonical.
    assert rarity_service.snap_to_vocab("quarter-century  secret rare") == "Quarter Century Secret Rare"
    assert rarity_service.snap_to_vocab("collectors rare") == "Collector's Rare"
    # Off-vocab garbage → None.
    assert rarity_service.snap_to_vocab("Mega Ultra Hyper Rare") is None
    assert rarity_service.snap_to_vocab("") is None


def test_rule_based_guess_quarter_century():
    m = schemas.RarityMeasurements(
        foil_line_angle_deg=4.0, foil_line_strength=3.0,
        name_is_champagne_gold=True, rectified=True,
    )
    rarity, alts, _why = rarity_service.rule_based_guess(m)
    assert rarity == "Quarter Century Secret Rare"
    assert "Platinum Secret Rare" in alts


def test_rule_based_guess_platinum_diagonal():
    m = schemas.RarityMeasurements(
        foil_line_angle_deg=45.0, foil_line_strength=3.0, rectified=True,
    )
    rarity, _alts, _why = rarity_service.rule_based_guess(m)
    assert rarity == "Platinum Secret Rare"


def test_rule_based_guess_prismatic_ultimate():
    m = schemas.RarityMeasurements(frame_varnish_energy=2.0, rectified=True)
    rarity, _alts, _why = rarity_service.rule_based_guess(m)
    assert rarity == "Prismatic Ultimate Rare"


def test_detect_rarity_vision_hybrid_snaps_and_clamps():
    client = StubClient(raw=(
        '{"rarity":"platinum secret rare","confidence":1.7,'
        '"alternatives":["Quarter Century Secret Rare","Bogus Rare"],'
        '"reasoning":"diagonal lines"}'
    ))
    m = schemas.RarityMeasurements(foil_line_angle_deg=45.0, rectified=True)
    res = rarity_service.detect_rarity(
        [(b"x", "image/jpeg")], "Crossout Designator", "Rarity Collection",
        client=client, measurements=m,
    )
    assert res.rarity == "Platinum Secret Rare"   # snapped to canonical
    assert res.confidence == 1.0                  # clamped to [0,1]
    assert res.alternatives == ["Quarter Century Secret Rare"]  # bogus filtered
    assert res.method == "hybrid"                 # measurements present


def test_detect_rarity_vision_only_without_measurements():
    client = StubClient(raw='{"rarity":"Ultra Rare","confidence":0.8}')
    res = rarity_service.detect_rarity(
        [(b"x", "image/jpeg")], "Some Card", None, client=client, measurements=None,
    )
    assert res.rarity == "Ultra Rare"
    assert res.method == "vision_only"


def test_detect_rarity_offvocab_falls_back_to_cv():
    client = StubClient(raw='{"rarity":"Totally Fake Rare","confidence":0.9}')
    m = schemas.RarityMeasurements(foil_line_angle_deg=45.0, foil_line_strength=3.0, rectified=True)
    res = rarity_service.detect_rarity(
        [(b"x", "image/jpeg")], "Card", None, client=client, measurements=m,
    )
    assert res.rarity == "Platinum Secret Rare"
    assert res.method == "cv_only"


def test_detect_rarity_cv_only_when_unconfigured():
    client = StubClient(configured=False)
    m = schemas.RarityMeasurements(frame_varnish_energy=2.0, rectified=True)
    res = rarity_service.detect_rarity(
        [(b"x", "image/jpeg")], "Card", None, client=client, measurements=m,
    )
    assert res.method == "cv_only"
    assert res.rarity == "Prismatic Ultimate Rare"
    assert res.confidence < 0.6


def test_detect_rarity_deepseek_error_falls_back():
    client = StubClient(raise_exc=DeepSeekVisionError("boom"))
    m = schemas.RarityMeasurements(foil_line_angle_deg=4.0, foil_line_strength=3.0,
                                   name_is_champagne_gold=True, rectified=True)
    res = rarity_service.detect_rarity(
        [(b"x", "image/jpeg")], "Card", None, client=client, measurements=m,
    )
    assert res.rarity == "Quarter Century Secret Rare"
    assert res.method == "cv_only"


# ---------------------------------------------------------------------------
# rarity_cv degrades safely with no/empty input
# ---------------------------------------------------------------------------

def test_rarity_cv_empty_frames_returns_all_none():
    import rarity_cv
    m = rarity_cv.measure([])
    assert m.foil_line_angle_deg is None
    assert m.rectified is False


# ---------------------------------------------------------------------------
# Scan orchestrator (scan_service.run_scan) with injected collaborators
# ---------------------------------------------------------------------------

def test_run_scan_assembles_ready_to_add():
    import scan_service

    frames = [(b"frame", "image/jpeg")]
    ident = schemas.IdentifyResult(
        source_filename="scan",
        candidates=[schemas.IdentifyCandidate(
            game="yugioh", name="Crossout Designator",
            set_name="Rarity Collection", confidence=0.92,
        )],
    )
    rarity = schemas.RarityResult(
        rarity="Platinum Secret Rare", confidence=0.81, method="hybrid",
    )
    price_hit = {
        "tcgplayer_price": 40.0, "price_source": "ygoprodeck:set_price",
        "external_id": "12345", "image_url": "http://img/small.jpg",
        "set_code": "RA04-EN001", "set_name": "Rarity Collection",
    }

    result = scan_service.run_scan(
        StubClient(configured=True),
        capture=lambda: frames,
        identifier=lambda client, fn, frs, hint: ident,
        measurer=lambda frs: schemas.RarityMeasurements(),
        rarity_detector=lambda frs, name, set_name, *, client, measurements: rarity,
        pricer=lambda **k: price_hit,
    )

    assert result.frames_captured == 1
    assert result.candidate.name == "Crossout Designator"
    assert result.rarity.rarity == "Platinum Secret Rare"
    assert result.price.tcgplayer_price == 40.0
    rta = result.ready_to_add
    assert rta.rarity == "Platinum Secret Rare"
    assert rta.is_foil is True            # secret rare → foil
    assert rta.card_number == "RA04-EN001"
    assert rta.external_source == "ygoprodeck"
    assert rta.external_id == "12345"


def test_run_scan_no_candidate_reports_error():
    import scan_service
    empty = schemas.IdentifyResult(source_filename="scan", candidates=[], error="nothing")
    result = scan_service.run_scan(
        StubClient(configured=True),
        capture=lambda: [(b"x", "image/jpeg")],
        identifier=lambda *a, **k: empty,
        measurer=lambda frs: schemas.RarityMeasurements(),
        rarity_detector=lambda *a, **k: None,
        pricer=lambda **k: None,
    )
    assert result.candidate is None
    assert result.error
    assert result.ready_to_add is None


# ---------------------------------------------------------------------------
# Endpoint wiring (camera 503 gate + reprice) via TestClient
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    os.environ["DISABLE_SCHEDULER"] = "1"
    test_db = Path(__file__).parent / "test_scan.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{test_db.as_posix()}"
    if test_db.exists():
        test_db.unlink()
    import database, models, main
    importlib.reload(database)
    importlib.reload(models)
    importlib.reload(main)
    models.Base.metadata.create_all(bind=database.engine)
    with TestClient(main.app) as c:
        yield c
    database.engine.dispose()
    if test_db.exists():
        try:
            test_db.unlink()
        except PermissionError:
            pass


def test_scan_preview_503_without_camera(client, monkeypatch):
    import camera_service
    monkeypatch.setattr(camera_service, "is_available", lambda: False)
    res = client.get("/scan/preview")
    assert res.status_code == 503


def test_scan_capture_503_without_camera(client, monkeypatch):
    import camera_service
    monkeypatch.setattr(camera_service, "is_available", lambda: False)
    res = client.post("/scan/capture")
    assert res.status_code == 503


def test_scan_capture_503_when_deepseek_missing(client, monkeypatch):
    import camera_service, main
    monkeypatch.setattr(camera_service, "is_available", lambda: True)
    monkeypatch.setattr(main.DeepSeekVision, "is_configured", lambda self: False)
    res = client.post("/scan/capture")
    assert res.status_code == 503


def test_scan_capture_happy(client, monkeypatch):
    import camera_service, main, scan_service
    monkeypatch.setattr(camera_service, "is_available", lambda: True)
    monkeypatch.setattr(main.DeepSeekVision, "is_configured", lambda self: True)
    canned = schemas.ScanResult(
        candidate=schemas.ScanCandidate(name="Crossout Designator",
                                        set_name="Rarity Collection", confidence=0.9),
        rarity=schemas.RarityResult(rarity="Platinum Secret Rare", confidence=0.8, method="hybrid"),
        price=schemas.ScanPrice(tcgplayer_price=40.0, source="ygoprodeck:set_price"),
        ready_to_add=schemas.CardCreate(
            name="Crossout Designator", set_name="Rarity Collection",
            rarity="Platinum Secret Rare", game="yugioh", is_foil=True,
        ),
        frames_captured=9,
    )
    monkeypatch.setattr(scan_service, "run_scan", lambda *a, **k: canned)
    res = client.post("/scan/capture")
    assert res.status_code == 200
    body = res.json()
    assert body["candidate"]["name"] == "Crossout Designator"
    assert body["rarity"]["rarity"] == "Platinum Secret Rare"
    assert body["price"]["tcgplayer_price"] == 40.0


def test_scan_reprice(client, monkeypatch):
    import scan_service
    monkeypatch.setattr(
        scan_service, "reprice",
        lambda req: schemas.ScanRepriceResponse(
            tcgplayer_price=15.0, set_name="Rarity Collection",
            source="ygoprodeck:set_price",
        ),
    )
    res = client.post("/scan/reprice", json={
        "external_id": "12345", "rarity": "Quarter Century Secret Rare",
    })
    assert res.status_code == 200
    assert res.json()["tcgplayer_price"] == 15.0


def test_scan_reprice_requires_id_or_name(client):
    res = client.post("/scan/reprice", json={"external_id": "", "rarity": "Rare"})
    assert res.status_code == 400
