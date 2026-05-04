"""End-to-end tests for the Card Collection API.

Each test uses an isolated TestClient against an ephemeral SQLite DB. The scheduler is
disabled via DISABLE_SCHEDULER so background threads don't race the test process.
"""
from __future__ import annotations

import os
import sys
import importlib
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


def test_yugioh_tcgplayer_url_picks_correct_printing(client, monkeypatch):
    """YGO URL with a specific set in the slug must pick that printing's set_name,
    not the first one in YGOPRODeck's card_sets list."""
    ygo_card = {
        "id": 37818794,
        "name": "Red-Eyes Dark Dragoon",
        "card_sets": [
            {"set_name": "2020 Tin of Lost Memories Mega Pack",
             "set_rarity": "Ultra Rare", "set_price": "115.97"},
            {"set_name": "25th Anniversary Rarity Collection II",
             "set_rarity": "Collector's Rare", "set_price": "0"},
            {"set_name": "Rarity Collection 5",
             "set_rarity": "Starlight Rare", "set_price": "0"},
        ],
        "card_images": [{"image_url_small": "https://example.test/dragoon.jpg"}],
        "card_prices": [{"tcgplayer_price": "1.19"}],
        "type": "Fusion Monster",
    }

    class Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
        def json(self):
            return self._payload

    seq = iter([
        Resp(200, {"marketPrice": 87.35, "rarityName": "Ultra Rare"}),  # TCG product API
        Resp(404, {"object": "error"}),     # Scryfall miss
        Resp(200, {"data": []}),            # PokemonTCG no match
        Resp(400, {"error": "no match"}),   # YGOPRODeck full-name miss
        Resp(200, {"data": [ygo_card]}),    # YGOPRODeck trailing-tokens hit
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
    assert body["external_source"] == "ygoprodeck"
    assert body["name"] == "Red-Eyes Dark Dragoon"
    # Critical: the URL specified "Rarity Collection 5" — picker must land there
    # and not on "25th Anniversary Rarity Collection II" or "Tin of Lost Memories".
    assert body["set_name"] == "Rarity Collection 5"
    # TCGplayer's own product API is the authoritative price source — overrides
    # whatever the per-game catalog had as a card-wide aggregate.
    assert body["rarity"] == "Ultra Rare"
    assert body["tcgplayer_price"] == 87.35


def test_yugioh_tcgplayer_url_strips_rarity_suffix(client, monkeypatch):
    """A TCGplayer URL whose slug ends with a rarity descriptor (e.g.
    ``...-dark-magician-starlight-rare``) must NOT degrade to a single-token
    YGOPRODeck search of "rare" — that returns "Rare Fish" alphabetically.

    Fix: prefer TCGplayer's own ``productName`` ("Dark Magician (Starlight Rare)")
    with the parenthetical stripped as the primary search query. The slug
    fallback also strips trailing rarity tokens defensively.
    """
    ygo_card = {
        "id": 46986414,
        "name": "Dark Magician",
        "card_sets": [
            {"set_name": "Rarity Collection 5",
             "set_rarity": "Starlight Rare", "set_price": "0"},
            {"set_name": "Legend of Blue Eyes White Dragon",
             "set_rarity": "Ultra Rare", "set_price": "150.00"},
        ],
        "card_images": [{"image_url_small": "https://example.test/dm.jpg"}],
        "card_prices": [{"tcgplayer_price": "1.50"}],
        "type": "Normal Monster",
    }

    class Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
        def json(self):
            return self._payload

    seq = iter([
        Resp(200, {  # TCGplayer product details — productName carries truth
            "marketPrice": 9.72,
            "rarityName": "Starlight Rare",
            "productName": "Dark Magician (Starlight Rare)",
            "productLineName": "YuGiOh",
            "setName": "Rarity Collection 5",
        }),
        Resp(404, {"object": "error"}),     # Scryfall miss (not magic)
        Resp(200, {"data": []}),            # PokemonTCG no match
        Resp(200, {"data": [ygo_card]}),    # YGOPRODeck on cleaned productName "dark magician"
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
    assert body["external_source"] == "ygoprodeck"
    assert body["name"] == "Dark Magician"
    # Set-token preference still pins the printing the URL identifies.
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
