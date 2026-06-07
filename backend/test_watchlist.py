"""End-to-end tests for the watchlist / price-alert endpoints.

Uses its own ephemeral DB (separate from test_api.py) and never touches a price
provider — current price is driven through the card's current_price column.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["DISABLE_SCHEDULER"] = "1"
TEST_DB = Path(__file__).parent / "test_watchlist.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"


@pytest.fixture(scope="module")
def client():
    if TEST_DB.exists():
        try:
            TEST_DB.unlink()
        except PermissionError:
            pass  # Windows: a sibling module's SQLite handle may still be releasing.
    # Set inside the fixture (not just at import) so this module's reload binds to
    # OUR db even when another test module set DATABASE_URL at its own import time.
    os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
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
            pass


def _make_card(client, price: float) -> int:
    res = client.post("/cards/", json={
        "name": "Charizard", "set_name": "Base Set", "game": "pokemon",
        "quantity": 1,
    })
    assert res.status_code == 200, res.text
    card_id = res.json()["id"]
    # Drive the "current price" via the update endpoint (no provider needed).
    upd = client.put(f"/cards/{card_id}", json={"current_price": price})
    assert upd.status_code == 200, upd.text
    return card_id


def test_add_list_delete_watch(client):
    card_id = _make_card(client, 100.0)
    res = client.post("/watchlist", json={
        "item_type": "card", "item_id": card_id,
        "direction": "drop", "threshold_pct": 10, "baseline_price": 100,
    })
    assert res.status_code == 200, res.text
    wid = res.json()["id"]
    assert res.json()["baseline_price"] == 100

    listing = client.get("/watchlist").json()
    assert any(w["id"] == wid for w in listing)

    assert client.delete(f"/watchlist/{wid}").status_code == 200
    assert all(w["id"] != wid for w in client.get("/watchlist").json())


def test_delete_missing_watch_404(client):
    assert client.delete("/watchlist/999999").status_code == 404


def test_alert_fires_then_acks_then_rearms(client):
    card_id = _make_card(client, 100.0)
    client.post("/watchlist", json={
        "item_type": "card", "item_id": card_id,
        "direction": "drop", "threshold_pct": 10, "baseline_price": 100,
    })

    # No move yet → no alert.
    assert not _alerts_for(client, card_id)

    # Drop 20% → alert fires.
    client.put(f"/cards/{card_id}", json={"current_price": 80})
    fired = _alerts_for(client, card_id)
    assert len(fired) == 1
    wid = fired[0]["id"]
    assert fired[0]["pct_change"] == -20.0

    # Acknowledge → muted, no longer listed.
    assert client.post(f"/watchlist/{wid}/ack").status_code == 200
    assert not _alerts_for(client, card_id)

    # Price recovers above threshold → entry re-arms (still no alert)...
    client.put(f"/cards/{card_id}", json={"current_price": 100})
    assert not _alerts_for(client, card_id)
    # ...and a fresh drop fires again.
    client.put(f"/cards/{card_id}", json={"current_price": 80})
    assert len(_alerts_for(client, card_id)) == 1


def test_baseline_defaults_to_current_price(client):
    card_id = _make_card(client, 50.0)
    res = client.post("/watchlist", json={
        "item_type": "card", "item_id": card_id, "threshold_pct": 5,
    })
    assert res.json()["baseline_price"] == 50.0


def _alerts_for(client, card_id: int) -> list[dict]:
    return [a for a in client.get("/alerts").json() if a["item_id"] == card_id]
