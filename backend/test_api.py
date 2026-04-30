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
