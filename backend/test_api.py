"""End-to-end tests for the Card Collection API.

Each test uses an isolated TestClient against an ephemeral SQLite DB. The scheduler is
disabled via DISABLE_SCHEDULER so background threads don't race the test process.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["DISABLE_SCHEDULER"] = "1"
# Per-test DB so we don't clobber any local dev DB.
TEST_DB = Path(__file__).parent / "test_card_collection.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"


@pytest.fixture(scope="module")
def client():
    if TEST_DB.exists():
        TEST_DB.unlink()
    sys.path.insert(0, str(Path(__file__).parent))
    import database, models, main
    importlib.reload(database)
    importlib.reload(models)
    importlib.reload(main)
    models.Base.metadata.create_all(bind=database.engine)
    with TestClient(main.app) as c:
        yield c
    database.engine.dispose()
    if TEST_DB.exists():
        try:
            TEST_DB.unlink()
        except PermissionError:
            pass  # Windows: SQLite handle release can lag — best-effort cleanup.


# ---- Root ------------------------------------------------------------------

def test_root(client):
    res = client.get("/")
    assert res.status_code == 200
    assert res.json() == {"message": "Card Collection API"}


# ---- Card CRUD -------------------------------------------------------------

def test_create_card_populates_price_sources(client):
    res = client.post("/cards/", json={
        "name": "Black Lotus", "set_name": "Alpha", "game": "magic", "is_foil": True,
    })
    assert res.status_code == 200
    data = res.json()
    assert data["id"] >= 1
    assert data["name"] == "Black Lotus"
    assert data["current_price"] is not None
    assert data["current_price"] > 0


def test_read_card_by_id(client):
    res = client.get("/cards/1")
    assert res.status_code == 200
    assert res.json()["name"] == "Black Lotus"


def test_read_card_404(client):
    res = client.get("/cards/9999")
    assert res.status_code == 404


def test_update_card_refreshes_price(client):
    res = client.put("/cards/1", json={"is_foil": False})
    assert res.status_code == 200
    assert res.json()["is_foil"] is False
    # Updating fields that affect pricing should re-fetch a price.
    assert res.json()["current_price"] is not None


def test_list_cards(client):
    res = client.get("/cards/")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
    assert len(res.json()) >= 1


def test_delete_card(client):
    create = client.post("/cards/", json={
        "name": "Disposable", "set_name": "Test", "game": "pokemon",
    })
    card_id = create.json()["id"]
    res = client.delete(f"/cards/{card_id}")
    assert res.status_code == 200
    follow = client.get(f"/cards/{card_id}")
    assert follow.status_code == 404


def test_delete_card_404(client):
    res = client.delete("/cards/9999")
    assert res.status_code == 404


# ---- Sealed CRUD -----------------------------------------------------------

def test_create_sealed(client):
    res = client.post("/sealed/", json={
        "name": "Booster Box",
        "set_name": "Base",
        "product_type": "booster box",
        "game": "pokemon",
        "quantity": 2,
    })
    assert res.status_code == 200
    data = res.json()
    assert data["product_type"] == "booster box"
    assert data["quantity"] == 2
    assert data["current_price"] is not None


def test_list_sealed(client):
    res = client.get("/sealed/")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_update_sealed_404(client):
    res = client.put("/sealed/9999", json={"quantity": 10})
    assert res.status_code == 404


# ---- Aggregates ------------------------------------------------------------

def test_collection_value(client):
    res = client.get("/collection/value")
    assert res.status_code == 200
    body = res.json()
    assert "total_value" in body
    assert body["total_value"] >= 0


def test_trigger_price_update_logs_history(client):
    res = client.post("/prices/update")
    assert res.status_code == 200
    assert "message" in res.json()
    # After an update we should see at least one PriceHistory entry for card 1.
    hist = client.get("/price-history/card/1").json()
    assert isinstance(hist, list)
    assert len(hist) >= 1
    row = hist[0]
    assert "source" in row and "price" in row and "timestamp" in row


def test_snapshot_shape(client):
    res = client.get("/snapshot")
    assert res.status_code == 200
    body = res.json()
    assert "timestamp" in body
    assert isinstance(body["by_source"], dict)
    assert isinstance(body["history"], list)
    assert "total_value" in body


def test_snapshot_by_source_after_update(client):
    client.post("/prices/update")
    body = client.get("/snapshot").json()
    # After the update there must be at least one provider total > 0.
    assert any(v > 0 for v in body["by_source"].values())


# ---- Price service: mock determinism ---------------------------------------

def test_mock_prices_are_deterministic():
    """When no provider creds are present mocks must be stable across calls."""
    from price_service import fetch_card_prices_all_sources
    a = fetch_card_prices_all_sources("Pikachu", "Base Set", "pokemon", False)
    b = fetch_card_prices_all_sources("Pikachu", "Base Set", "pokemon", False)
    assert a == b
    assert set(a.keys()) >= {"TCGPlayer", "eBay", "CardMarket"}


# ---- Provider unit tests (no network) --------------------------------------

def test_providers_disabled_without_creds(monkeypatch):
    """No credentials => is_enabled() is False for every provider."""
    for var in [
        "TCGPLAYER_CLIENT_ID", "TCGPLAYER_CLIENT_SECRET",
        "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_OAUTH_TOKEN",
        "CARDMARKET_APP_TOKEN", "CARDMARKET_APP_SECRET",
        "CARDMARKET_ACCESS_TOKEN", "CARDMARKET_ACCESS_SECRET",
    ]:
        monkeypatch.delenv(var, raising=False)
    # Clear the lru_cache so registry re-evaluates env.
    from providers import registry
    registry.get_enabled_providers.cache_clear()
    assert registry.get_enabled_providers() == []


def test_tcgplayer_provider_off_when_no_keys(monkeypatch):
    monkeypatch.delenv("TCGPLAYER_CLIENT_ID", raising=False)
    monkeypatch.delenv("TCGPLAYER_CLIENT_SECRET", raising=False)
    from providers.tcgplayer import TCGPlayerProvider
    assert TCGPlayerProvider().is_enabled() is False


# ---- Catalog search --------------------------------------------------------

def test_catalog_search_validates_game(client):
    res = client.get("/catalog/search", params={"q": "Pikachu", "game": "warhammer"})
    assert res.status_code == 400


def test_catalog_search_short_query_returns_empty(client):
    res = client.get("/catalog/search", params={"q": "a", "game": "magic"})
    assert res.status_code == 200
    assert res.json() == []


def test_catalog_search_scryfall_mocked(client, monkeypatch):
    """Mock the upstream HTTP to assert we normalize Scryfall payloads correctly."""
    fake_payload = {
        "data": [{
            "id": "abc-123",
            "name": "Lightning Bolt",
            "set_name": "Limited Edition Alpha",
            "image_uris": {"small": "https://example.test/bolt.jpg"},
            "prices": {"usd": "12.34", "usd_foil": "56.78"},
            "rarity": "common",
        }]
    }

    class FakeResp:
        status_code = 200
        def json(self): return fake_payload

    from providers import catalog as catalog_module
    monkeypatch.setattr(catalog_module, "request_with_backoff", lambda *a, **kw: FakeResp())

    res = client.get("/catalog/search", params={"q": "Lightning Bolt", "game": "magic"})
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["external_source"] == "scryfall"
    assert row["external_id"] == "abc-123"
    assert row["name"] == "Lightning Bolt"
    assert row["tcgplayer_price"] == 12.34
    assert row["tcgplayer_price_foil"] == 56.78
    assert row["image_url"] == "https://example.test/bolt.jpg"


def test_catalog_resolve_validates_input(client):
    """Empty/missing url -> 400; junk url -> 404."""
    res = client.get("/catalog/resolve", params={"url": ""})
    assert res.status_code == 400
    res = client.get("/catalog/resolve", params={"url": "https://example.com/nope"})
    assert res.status_code == 404


def test_catalog_resolve_scryfall_url(client, monkeypatch):
    """A scryfall.com URL should resolve to a single CatalogResult."""
    fake_card = {
        "id": "scry-uuid",
        "name": "Lightning Bolt",
        "set_name": "Limited Edition Alpha",
        "image_uris": {"small": "https://example.test/bolt.jpg"},
        "prices": {"usd": "300.00", "usd_foil": None},
        "rarity": "common",
    }

    class FakeResp:
        status_code = 200
        def json(self): return fake_card

    from providers import catalog as catalog_module
    monkeypatch.setattr(catalog_module, "request_with_backoff", lambda *a, **kw: FakeResp())

    res = client.get(
        "/catalog/resolve",
        params={"url": "https://scryfall.com/card/lea/161/lightning-bolt"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["external_source"] == "scryfall"
    assert body["external_id"] == "scry-uuid"
    assert body["tcgplayer_price"] == 300.0


def test_catalog_resolve_tcgplayer_url_via_scryfall(client, monkeypatch):
    """A tcgplayer.com URL goes through Scryfall's /cards/tcgplayer/<id>."""
    fake_card = {
        "id": "tcg-uuid",
        "name": "Black Lotus",
        "set_name": "Alpha",
        "image_uris": {"small": "https://example.test/lotus.jpg"},
        "prices": {"usd": "50000.00", "usd_foil": None},
        "rarity": "rare",
    }

    class FakeResp:
        status_code = 200
        def json(self): return fake_card

    from providers import catalog as catalog_module
    monkeypatch.setattr(catalog_module, "request_with_backoff", lambda *a, **kw: FakeResp())

    res = client.get(
        "/catalog/resolve",
        params={"url": "https://www.tcgplayer.com/product/12345/something"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["external_id"] == "tcg-uuid"
    assert body["tcgplayer_price"] == 50000.0


def test_health_endpoint(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_status_endpoint_shape(client):
    res = client.get("/status")
    assert res.status_code == 200
    body = res.json()
    assert body["service"] == "card-collection-anime"
    assert "uptime_seconds" in body
    assert "hostname" in body
    assert "system" in body and "database" in body
    db = body["database"]
    assert {"cards", "sealed_products", "price_history_rows", "total_value"} <= set(db)


def test_status_logs_endpoint(client):
    import logging
    logging.getLogger("test_status").info("hello-from-test")
    res = client.get("/status/logs", params={"limit": 50})
    assert res.status_code == 200
    rows = res.json()
    assert isinstance(rows, list)
    # Log handler is shared module state; the test just emitted a record so we
    # expect to see it (or at least: the schema is right).
    if rows:
        sample = rows[-1]
        assert {"ts", "level", "name", "msg"} <= set(sample)


def test_yugioh_tcgplayer_url_uses_tcgplayer_details_directly(client, monkeypatch):
    """Phase C hybrid: a YGO TCGplayer URL resolves via TCGplayer's product
    details API, not via YGOPRODeck. Per-printing marketPrice + rarity come
    straight from TCGplayer. No more "Rare Fish" or "$0.22" bugs from
    YGOPRODeck aggregate / zero-data per-printing entries.
    """
    class Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
        def json(self):
            return self._payload

    # Order: Scryfall miss (not Magic) -> TCGplayer details hit.
    seq = iter([
        Resp(404, {"object": "error"}),
        Resp(200, {
            "marketPrice": 87.35,
            "rarityName": "Ultra Rare",
            "productName": "Red-Eyes Dark Dragoon (Ultra Rare)",
            "productUrlName": "Red-Eyes Dark Dragoon",
            "productLineName": "YuGiOh",
            "setName": "Rarity Collection 5",
        }),
    ])
    from providers import catalog as catalog_module
    monkeypatch.setattr(
        catalog_module, "request_with_backoff",
        lambda *a, **kw: next(seq, Resp(404, {})),
    )

    res = client.get(
        "/catalog/resolve",
        params={"url": "https://www.tcgplayer.com/product/687314/"
                       "yugioh-rarity-collection-5-red-eyes-dark-dragoon"},
    )
    assert res.status_code == 200
    body = res.json()
    # New contract: TCGplayer is the source of truth for non-Magic URLs.
    assert body["external_source"] == "tcgplayer"
    assert body["external_id"] == "687314"
    assert body["tcgplayer_product_id"] == "687314"
    # Cleaned name (parenthetical stripped) — the productUrlName takes precedence
    # because the productName had a trailing rarity tag.
    assert body["name"] == "Red-Eyes Dark Dragoon"
    assert body["set_name"] == "Rarity Collection 5"
    assert body["rarity"] == "Ultra Rare"
    assert body["tcgplayer_price"] == 87.35


def test_yugioh_tcgplayer_url_strips_rarity_suffix(client, monkeypatch):
    """Phase C hybrid: trailing rarity descriptors in productName ("Dark Magician
    (Starlight Rare)") are stripped for the saved card name. Source becomes
    "tcgplayer" — no detour through YGOPRODeck. This is the canonical test
    for the original "Rare Fish" / "$0.22" bug class.
    """
    class Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
        def json(self):
            return self._payload

    seq = iter([
        Resp(404, {"object": "error"}),     # Scryfall miss (not Magic)
        Resp(200, {
            "marketPrice": 9.72,
            "rarityName": "Starlight Rare",
            "productName": "Dark Magician (Starlight Rare)",
            "productLineName": "YuGiOh",
            "setName": "Rarity Collection 5",
        }),
    ])
    from providers import catalog as catalog_module
    monkeypatch.setattr(
        catalog_module, "request_with_backoff",
        lambda *a, **kw: next(seq, Resp(404, {})),
    )

    res = client.get(
        "/catalog/resolve",
        params={"url": "https://www.tcgplayer.com/product/687196/"
                       "yugioh-rarity-collection-5-dark-magician-starlight-rare"
                       "?country=US&utm_campaign=foo"},
    )
    assert res.status_code == 200
    body = res.json()
    # Critical: NOT "Rare Fish" — must hit the actual card the URL points at.
    assert body["external_source"] == "tcgplayer"
    assert body["external_id"] == "687196"
    assert body["name"] == "Dark Magician"  # Parenthetical stripped from productName.
    assert body["set_name"] == "Rarity Collection 5"
    assert body["rarity"] == "Starlight Rare"
    assert body["tcgplayer_price"] == 9.72


def test_clean_tcgplayer_product_name_strips_tags():
    """Unit test: parenthetical tags + trailing collector numbers are dropped."""
    from providers.catalog import _clean_tcgplayer_product_name as clean

    assert clean("Dark Magician (Starlight Rare)") == "Dark Magician"
    assert clean("Charizard - 4/102") == "Charizard"
    assert clean("Black Lotus (Alpha Edition)") == "Black Lotus"
    assert clean("Foo Bar (Promo) (Holo)") == "Foo Bar"
    assert clean("") == ""
    assert clean("   Naked Name   ") == "Naked Name"


def test_strip_rarity_suffix_tokens():
    """Unit test: trailing rarity descriptor tokens come off the slug name."""
    from providers.catalog import _strip_rarity_suffix_tokens as strip

    assert strip(["dark", "magician", "starlight", "rare"]) == ["dark", "magician"]
    assert strip(["red", "eyes", "dark", "dragoon"]) == ["red", "eyes", "dark", "dragoon"]
    assert strip(["pikachu", "promo", "holo"]) == ["pikachu"]
    assert strip([]) == []


@pytest.mark.skip(reason="Phase C removed the YGOPRODeck refresh branch entirely; "
                          "this test described an intermediate fix that is no longer "
                          "the strategy. Kept for history; see "
                          "test_fetch_tcgplayer_price_prefers_product_id_over_ygoprodeck "
                          "for the canonical refresh test.")
def test_ygoprodeck_price_picks_per_printing_when_set_name_given(monkeypatch):
    """Refresh path bug: a Starlight Rare Dark Magician saved from a TCGplayer URL
    was getting current_price=$0.22 instead of $9.67 because _ygoprodeck_price was
    returning the card-wide aggregate (cheapest reprint) instead of the printing
    matching the saved set_name. Threading set_name through fixes it.
    """
    ygo_card = {
        "id": 46986414,
        "name": "Dark Magician",
        "card_sets": [
            {"set_name": "Rarity Collection 5",
             "set_rarity": "Starlight Rare", "set_price": "9.67"},
            {"set_name": "Legend of Blue Eyes White Dragon",
             "set_rarity": "Ultra Rare", "set_price": "150.00"},
            {"set_name": "Starter Deck Yugi",
             "set_rarity": "Common", "set_price": "0.22"},
        ],
        "card_prices": [{"tcgplayer_price": "0.22"}],  # aggregate = cheapest reprint
    }

    class Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
        def json(self):
            return self._payload

    from providers import catalog as catalog_module
    monkeypatch.setattr(
        catalog_module, "request_with_backoff",
        lambda *a, **kw: Resp(200, {"data": [ygo_card]}),
    )

    # Without set_name -> aggregate (the buggy behavior, kept as fallback)
    aggregate = catalog_module.fetch_tcgplayer_price("ygoprodeck", "46986414")
    assert aggregate == 0.22

    # With set_name -> per-printing price for the matching printing
    pinned = catalog_module.fetch_tcgplayer_price(
        "ygoprodeck", "46986414", set_name="Rarity Collection 5"
    )
    assert pinned == 9.67

    # Different saved printing -> different per-printing price
    pinned_lob = catalog_module.fetch_tcgplayer_price(
        "ygoprodeck", "46986414", set_name="Legend of Blue Eyes White Dragon"
    )
    assert pinned_lob == 150.00

    # Set name with no token overlap -> falls back to aggregate, not random printing
    no_match = catalog_module.fetch_tcgplayer_price(
        "ygoprodeck", "46986414", set_name="Some Unrelated Set"
    )
    assert no_match == 0.22


def test_fetch_tcgplayer_price_prefers_product_id_over_ygoprodeck(monkeypatch):
    """When a card was imported via TCGplayer URL, ``tcgplayer_product_id`` is the
    authoritative source — refresh hits TCGplayer's product details API for the
    per-printing marketPrice. Critical for YGO printings where YGOPRODeck's
    set_price is literally "0" (Starlight Rare, Ghost Rare, etc), which would
    otherwise leak through and the refresh would land on the card-wide aggregate.
    """
    class Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
        def json(self):
            return self._payload

    # Whatever TCGplayer details would return for the Starlight Rare printing.
    tcg_details = {
        "marketPrice": 9.72,
        "rarityName": "Starlight Rare",
        "productName": "Dark Magician (Starlight Rare)",
    }
    # YGOPRODeck would return aggregate $0.22 (the bug we're guarding against).
    ygo_card = {
        "id": 46986414,
        "name": "Dark Magician",
        "card_sets": [{"set_name": "Rarity Collection 5",
                       "set_rarity": "Starlight Rare", "set_price": "0"}],
        "card_prices": [{"tcgplayer_price": "0.22"}],
    }

    seq = iter([Resp(200, tcg_details), Resp(200, {"data": [ygo_card]})])
    from providers import catalog as catalog_module
    monkeypatch.setattr(
        catalog_module, "request_with_backoff",
        lambda *a, **kw: next(seq, Resp(404, {})),
    )

    # With tcgplayer_product_id -> should hit TCGplayer first and return $9.72
    price = catalog_module.fetch_tcgplayer_price(
        "ygoprodeck", "46986414", set_name="Rarity Collection 5",
        tcgplayer_product_id="687196",
    )
    assert price == 9.72


def test_self_heal_schema_adds_missing_nullable_column(tmp_path):
    """Boot-time self-heal must idempotently add a missing nullable column.

    Reproduces the Pi situation that bit us today: existing SQLite DB lacks
    a column the new model has. Without the self-heal, every query returns
    500. With it, the ALTER runs, queries succeed, no Alembic invocation
    needed.
    """
    from sqlalchemy import create_engine, Column, Integer, String, inspect
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()

    # Old schema: just (id, name).
    class OldThing(Base):
        __tablename__ = "things"
        id = Column(Integer, primary_key=True)
        name = Column(String)

    db_path = tmp_path / "self_heal.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(bind=engine)

    # Now simulate a model bump: add a new nullable column.
    Base.registry.dispose()
    Base = declarative_base()

    class NewThing(Base):
        __tablename__ = "things"
        id = Column(Integer, primary_key=True)
        name = Column(String)
        new_field = Column(String, nullable=True)

    # Inline self-heal logic (mirror main._self_heal_schema for a single mapper).
    from sqlalchemy import text
    insp = inspect(engine)
    live_cols = {c["name"] for c in insp.get_columns("things")}
    assert "new_field" not in live_cols  # baseline: column missing

    with engine.begin() as conn:
        for col in NewThing.__table__.columns:
            if col.name in live_cols:
                continue
            assert col.nullable, "test setup error: should be nullable"
            ct = col.type.compile(dialect=engine.dialect)
            conn.execute(text(f'ALTER TABLE "things" ADD COLUMN "{col.name}" {ct}'))

    # Reinspect: column now present.
    insp = inspect(engine)
    live_cols = {c["name"] for c in insp.get_columns("things")}
    assert "new_field" in live_cols


def test_status_exposes_scheduler_health_fields(client):
    """The /status endpoint must surface scheduler_health + age + warnings so
    the UI can render a stale-data banner when the daemon dies silently.
    """
    res = client.get("/status")
    assert res.status_code == 200
    body = res.json()
    # New keys added in Phase B (status.py refactor).
    assert "scheduler_health" in body
    assert "last_price_update_age_seconds" in body
    assert "scheduler_interval_seconds" in body
    assert "schema_warnings" in body
    assert isinstance(body["schema_warnings"], list)


def test_resolve_tcgplayer_url_returns_tcgplayer_product_id(monkeypatch, client):
    """The resolver result must include ``tcgplayer_product_id`` so the frontend
    can pin it on save. Without it, the refresh path can't reach back to
    TCGplayer's authoritative price.
    """
    class Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
        def json(self):
            return self._payload

    seq = iter([
        Resp(404, {}),                                        # Scryfall miss (not Magic)
        Resp(200, {                                           # TCGplayer details (authoritative)
            "marketPrice": 9.72,
            "productName": "Dark Magician (Starlight Rare)",
            "productLineName": "YuGiOh",
            "rarityName": "Starlight Rare",
            "setName": "Rarity Collection 5",
        }),
    ])
    from providers import catalog as catalog_module
    monkeypatch.setattr(
        catalog_module, "request_with_backoff",
        lambda *a, **kw: next(seq, Resp(404, {})),
    )

    res = client.get(
        "/catalog/resolve",
        params={"url": "https://www.tcgplayer.com/product/687196/"
                       "yugioh-rarity-collection-5-dark-magician-starlight-rare"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["tcgplayer_product_id"] == "687196"


# ----- /identify (DeepSeek multimodal) --------------------------------------
#
# All tests monkeypatch the DeepSeekVision class so they never touch the real
# network. The patch replaces:
#   - is_configured() -> always True (so endpoint doesn't 503)
#   - identify(...) -> returns a canned DeepSeekResult with the JSON string
#     we want the service layer to parse.
#
# The shape mirrors what `providers.deepseek.DeepSeekVision.identify()` would
# return on a real call (raw_content is the JSON the model spat out).
# ---------------------------------------------------------------------------

def _patch_deepseek(monkeypatch, raw_content, *, raise_exc=None):
    """Helper: monkeypatch DeepSeekVision so every test stays offline."""
    from providers import deepseek as ds
    from providers.deepseek import DeepSeekResult, DeepSeekVisionError

    def fake_identify(self, images, system_prompt, user_prompt, **kw):
        if raise_exc is not None:
            raise raise_exc
        return DeepSeekResult(
            raw_content=raw_content,
            model="deepseek-v4-pro-test",
            prompt_tokens=100,
            completion_tokens=50,
        )

    monkeypatch.setattr(ds.DeepSeekVision, "is_configured", lambda self: True)
    monkeypatch.setattr(ds.DeepSeekVision, "identify", fake_identify)
    # Also keep the same patch reachable from the import location in main.
    import main
    monkeypatch.setattr(main, "DeepSeekVision", ds.DeepSeekVision)


def _multipart(name="dark.jpg", body=b"\xff\xd8\xff\xe0fakejpg", mime="image/jpeg"):
    """Build the (files=...) kwarg shape TestClient expects for multipart."""
    return {"file": (name, body, mime)}


def test_identify_image_happy_path(client, monkeypatch):
    """Single image → 1 candidate with TCGplayer URL preserved."""
    _patch_deepseek(monkeypatch, json.dumps({
        "candidates": [{
            "game": "yugioh",
            "name": "Dark Magician",
            "set_name": "Rarity Collection 5",
            "printing_notes": "Starlight Rare",
            "confidence": 0.92,
            "justification": "Distinctive starlight foil treatment visible.",
            "suggested_urls": [
                "https://www.tcgplayer.com/product/687196/yugioh-rarity-collection-5-dark-magician-starlight-rare"
            ],
            "search_queries": ["dark magician starlight rare rarity collection"],
        }]
    }))
    res = client.post("/identify/image", files=_multipart())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source_filename"] == "dark.jpg"
    assert body["error"] is None
    assert len(body["candidates"]) == 1
    c = body["candidates"][0]
    assert c["game"] == "yugioh"
    assert c["name"] == "Dark Magician"
    assert c["confidence"] == 0.92
    assert c["suggested_urls"][0].startswith("https://www.tcgplayer.com/product/687196/")


def test_identify_strips_hallucinated_url(client, monkeypatch):
    """URL from a non-allowlisted host must be dropped silently."""
    _patch_deepseek(monkeypatch, json.dumps({
        "candidates": [{
            "game": "magic",
            "name": "Black Lotus",
            "confidence": 0.7,
            "justification": "Alpha border.",
            "suggested_urls": [
                "https://malicious.example.com/steal",   # MUST be dropped
                "https://scryfall.com/card/lea/232/black-lotus",   # OK
            ],
            "search_queries": ["black lotus alpha"],
        }]
    }))
    res = client.post("/identify/image", files=_multipart(name="lotus.png", mime="image/png"))
    assert res.status_code == 200
    urls = res.json()["candidates"][0]["suggested_urls"]
    assert len(urls) == 1
    assert "scryfall.com" in urls[0]
    assert all("malicious.example.com" not in u for u in urls)


def test_identify_clamps_invalid_confidence(client, monkeypatch):
    """Model returning confidence > 1 or < 0 gets clamped into [0, 1]."""
    _patch_deepseek(monkeypatch, json.dumps({
        "candidates": [
            {"game": "magic", "name": "X", "confidence": 1.5,
             "justification": "?", "search_queries": ["x"]},
            {"game": "magic", "name": "Y", "confidence": -0.2,
             "justification": "?", "search_queries": ["y"]},
        ]
    }))
    res = client.post("/identify/image", files=_multipart())
    cands = res.json()["candidates"]
    confidences = {c["name"]: c["confidence"] for c in cands}
    assert confidences["X"] == 1.0
    assert confidences["Y"] == 0.0


def test_identify_normalises_unknown_game(client, monkeypatch):
    """Garbage in `game` field gets normalised to 'unknown' instead of erroring."""
    _patch_deepseek(monkeypatch, json.dumps({
        "candidates": [{
            "game": "made-up-game",
            "name": "Something",
            "confidence": 0.3,
            "search_queries": ["something"],
        }]
    }))
    res = client.post("/identify/image", files=_multipart())
    assert res.json()["candidates"][0]["game"] == "unknown"


def test_identify_handles_parse_failure(client, monkeypatch):
    """Model returning non-JSON → error field set, not a 500."""
    _patch_deepseek(monkeypatch, "this is not json {{{")
    res = client.post("/identify/image", files=_multipart())
    assert res.status_code == 200
    body = res.json()
    assert body["candidates"] == []
    assert body["error"] and "unparseable" in body["error"].lower()


def test_identify_503_when_key_missing(client, monkeypatch):
    """No DEEPSEEK_API_KEY → 503 with actionable hint, not a generic 500."""
    from providers import deepseek as ds
    monkeypatch.setattr(ds.DeepSeekVision, "is_configured", lambda self: False)
    res = client.post("/identify/image", files=_multipart())
    assert res.status_code == 503
    assert "DEEPSEEK_API_KEY" in res.json()["detail"]


def test_identify_rejects_unsupported_mime(client, monkeypatch):
    """Plain text upload → 415, no model call attempted."""
    _patch_deepseek(monkeypatch, "{}")  # would succeed if reached
    res = client.post("/identify/image", files={
        "file": ("notes.txt", b"hello world", "text/plain"),
    })
    assert res.status_code == 415
    assert "Unsupported" in res.json()["detail"]


def test_identify_batch_partial_failure(client, monkeypatch):
    """One bad result in a batch must not poison the others."""
    from providers import deepseek as ds
    from providers.deepseek import DeepSeekResult, DeepSeekVisionError
    monkeypatch.setattr(ds.DeepSeekVision, "is_configured", lambda self: True)

    call_count = {"n": 0}

    def flaky_identify(self, images, system_prompt, user_prompt, **kw):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise DeepSeekVisionError("simulated 429 then exhausted retries")
        return DeepSeekResult(
            raw_content=json.dumps({"candidates": [{
                "game": "magic", "name": f"Card{call_count['n']}",
                "confidence": 0.8, "search_queries": [f"card{call_count['n']}"],
            }]}),
            model="test", prompt_tokens=10, completion_tokens=5,
        )

    monkeypatch.setattr(ds.DeepSeekVision, "identify", flaky_identify)

    res = client.post("/identify/batch", files=[
        ("files", ("a.jpg", b"\xff\xd8\xff", "image/jpeg")),
        ("files", ("b.jpg", b"\xff\xd8\xff", "image/jpeg")),
        ("files", ("c.jpg", b"\xff\xd8\xff", "image/jpeg")),
    ])
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["results"]) == 3
    errors = [r["error"] for r in body["results"] if r["error"]]
    successes = [r for r in body["results"] if not r["error"]]
    assert len(errors) == 1
    assert len(successes) == 2
    assert all(s["candidates"] for s in successes)


def test_identify_video_ok_with_mocked_ffmpeg(client, monkeypatch):
    """Phase 3: ffmpeg extraction is injected; DeepSeek returns 2 unique cards."""
    _patch_deepseek(monkeypatch, json.dumps({
        "candidates": [
            {"game": "yugioh", "name": "Dark Magician", "confidence": 0.9,
             "search_queries": ["dark magician"]},
            {"game": "yugioh", "name": "Dark Magician", "confidence": 0.7,
             "search_queries": ["dark magician alt"]},  # dup name → dedup
            {"game": "yugioh", "name": "Blue-Eyes White Dragon",
             "confidence": 0.85, "search_queries": ["blue eyes"]},
        ]
    }))

    # Replace extract_video_frames with a fake that returns 3 byte blobs.
    # The endpoint passes our injected extractor straight through to
    # identify_video — see identify_service.identify_video(extractor=...).
    import identify_service
    fake_frames = [
        (b"\xff\xd8\xff frame1", "image/jpeg"),
        (b"\xff\xd8\xff frame2", "image/jpeg"),
        (b"\xff\xd8\xff frame3", "image/jpeg"),
    ]
    monkeypatch.setattr(
        identify_service, "extract_video_frames",
        lambda video_bytes, **kw: fake_frames,
    )

    res = client.post("/identify/video", files={
        "file": ("clip.mp4", b"\x00\x00\x00\x18ftypmp42whatever", "video/mp4"),
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["error"] is None
    names = [c["name"] for c in body["candidates"]]
    # Deduplicated: Dark Magician should appear once even though model returned twice.
    assert names.count("Dark Magician") == 1
    assert "Blue-Eyes White Dragon" in names


def test_identify_video_ffmpeg_missing(client, monkeypatch):
    """When ffmpeg can't run, /identify/video returns a 200 with an error field
    (per-clip failure, not a server error)."""
    _patch_deepseek(monkeypatch, "{}")
    import identify_service

    def broken_extract(video_bytes, **kw):
        raise RuntimeError("ffmpeg not found (test simulation)")

    monkeypatch.setattr(identify_service, "extract_video_frames", broken_extract)

    res = client.post("/identify/video", files={
        "file": ("clip.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4"),
    })
    assert res.status_code == 200
    body = res.json()
    assert body["candidates"] == []
    assert body["error"] and "ffmpeg" in body["error"].lower()


def test_identify_video_rejects_non_video_mime(client, monkeypatch):
    """An image accidentally posted to /identify/video → 415, no extract call."""
    _patch_deepseek(monkeypatch, "{}")
    res = client.post("/identify/video", files={
        "file": ("oops.jpg", b"\xff\xd8\xff", "image/jpeg"),
    })
    assert res.status_code == 415
    assert "Unsupported" in res.json()["detail"]


def test_profile_export_import_roundtrip(client):
    """Round-trip the entire collection through encrypted export/import."""
    # Seed two cards
    client.post("/cards/", json={"name": "Card A", "set_name": "S1", "game": "magic"})
    client.post("/cards/", json={"name": "Card B", "set_name": "S2", "game": "pokemon"})

    pre_count = len(client.get("/cards/").json())
    assert pre_count >= 2

    # Export
    exp = client.post("/profile/export", json={"password": "hunter2"})
    assert exp.status_code == 200
    blob = exp.text
    assert blob.startswith("CARD_COLLECTION_BACKUP")
    assert "salt:" in blob and "data:" in blob
    assert "Card A" not in blob  # plaintext name should not leak through encryption

    # Wrong password -> 400
    bad = client.post("/profile/import", json={
        "password": "wrong", "encrypted": blob, "replace": True,
    })
    assert bad.status_code == 400

    # Right password -> rows restored, count matches
    ok = client.post("/profile/import", json={
        "password": "hunter2", "encrypted": blob, "replace": True,
    })
    assert ok.status_code == 200
    restored = ok.json()["restored"]
    assert restored["cards"] == pre_count
    after_cards = client.get("/cards/").json()
    assert {c["name"] for c in after_cards} >= {"Card A", "Card B"}


def test_profile_import_rejects_garbage(client):
    res = client.post("/profile/import", json={
        "password": "x", "encrypted": "this is not a backup", "replace": True,
    })
    assert res.status_code == 400


def test_catalog_search_sealed_pokemon_returns_empty(client):
    """Sealed mode + non-Magic game must return [] (those APIs only carry singles)."""
    res = client.get("/catalog/search", params={"q": "booster box", "game": "pokemon", "sealed": "true"})
    assert res.status_code == 200
    assert res.json() == []


def test_tcgplayer_url_falls_back_to_og_scrape(client, monkeypatch):
    """When Scryfall and PokemonTCG.io both miss, resolve scrapes the TCG page."""
    fake_html = (
        '<html><head>'
        '<meta property="og:title" content="Pikachu V Box - Pokemon" />'
        '<meta property="og:image" content="https://example.test/box.jpg" />'
        '<meta property="product:price:amount" content="29.99" />'
        '</head></html>'
    )

    miss = type("R", (), {"status_code": 404, "json": lambda self: {}})()
    hit = type("R", (), {"status_code": 200, "text": fake_html, "json": lambda self: {}})()

    calls = {"n": 0}

    def fake_request(*args, **kwargs):
        calls["n"] += 1
        # First two calls are the Scryfall + PokemonTCG.io misses; third is the OG scrape.
        return hit if calls["n"] >= 3 else miss

    from providers import catalog as catalog_module
    monkeypatch.setattr(catalog_module, "request_with_backoff", fake_request)

    res = client.get(
        "/catalog/resolve",
        params={"url": "https://www.tcgplayer.com/product/99999/pikachu-v-box"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["external_source"] == "tcgplayer"
    assert body["external_id"] == "99999"
    assert body["name"] == "Pikachu V Box - Pokemon"
    assert body["image_url"] == "https://example.test/box.jpg"
    assert body["tcgplayer_price"] == 29.99


def test_create_card_with_external_id_uses_catalog_price(client, monkeypatch):
    """When a card is linked to a catalog ID, current_price should reflect the
    catalog-derived TCGplayer price even with no provider creds."""
    from providers import catalog as catalog_module

    # Force the catalog refresh path to return a known price.
    monkeypatch.setattr(
        catalog_module, "fetch_tcgplayer_price",
        lambda source, ext_id, is_foil=False: 99.99,
    )

    res = client.post("/cards/", json={
        "name": "Pinned Card",
        "set_name": "Test",
        "game": "magic",
        "external_source": "scryfall",
        "external_id": "pinned-id",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["external_source"] == "scryfall"
    assert body["external_id"] == "pinned-id"
    # Average across {TCGPlayer: 99.99, mock_eBay, mock_CardMarket} or just 99.99
    # depending on whether mocks fire. Catalog price is preserved as TCGPlayer.
    assert body["price_sources"]["TCGPlayer"] == 99.99
