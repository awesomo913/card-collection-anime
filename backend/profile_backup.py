"""Encrypted collection backup — export to / restore from a password-protected
text file.

Format (UTF-8 text, line-oriented so the file is human-inspectable):

    CARD_COLLECTION_BACKUP v1
    salt: <urlsafe-b64 16 bytes>
    iterations: <int>
    data: <urlsafe-b64 Fernet token>

Key derivation: PBKDF2-HMAC-SHA256 from the user's password + per-file salt.
Body is JSON; encrypted with Fernet (AES-128-CBC + HMAC-SHA256).

A user without the password sees only ciphertext; with the password the file
loads cleanly even after schema migrations (we keep the JSON shape additive).
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List, Tuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy.orm import Session

import models

BACKUP_VERSION = 1
HEADER = f"CARD_COLLECTION_BACKUP v{BACKUP_VERSION}"
DEFAULT_ITERATIONS = 600_000

_CARD_COLUMNS = [
    "name", "set_name", "card_number", "rarity", "condition", "quantity",
    "purchase_price", "current_price", "is_foil", "is_signed", "game",
    "notes", "price_sources", "external_source", "external_id", "image_url",
]
_SEALED_COLUMNS = [
    "name", "set_name", "product_type", "quantity", "purchase_price",
    "current_price", "game", "notes", "price_sources",
    "external_source", "external_id", "image_url",
]


# ---------- key derivation ----------

def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _row_to_dict(row, columns) -> Dict[str, Any]:
    return {col: getattr(row, col, None) for col in columns}


# ---------- export ----------

def export_profile(db: Session, password: str) -> str:
    if not password:
        raise ValueError("password is required")

    payload: Dict[str, Any] = {
        "version": BACKUP_VERSION,
        "cards": [_row_to_dict(c, _CARD_COLUMNS) for c in db.query(models.Card).all()],
        "sealed_products": [
            _row_to_dict(s, _SEALED_COLUMNS) for s in db.query(models.SealedProduct).all()
        ],
        "price_history": [
            {
                "item_type": h.item_type,
                "item_id": h.item_id,
                "source": h.source,
                "price": h.price,
                "timestamp": h.timestamp.isoformat() if h.timestamp else None,
            }
            for h in db.query(models.PriceHistory).all()
        ],
    }

    salt = os.urandom(16)
    key = _derive_key(password, salt, DEFAULT_ITERATIONS)
    token = Fernet(key).encrypt(json.dumps(payload, default=str).encode("utf-8"))

    return (
        f"{HEADER}\n"
        f"salt: {base64.urlsafe_b64encode(salt).decode('ascii')}\n"
        f"iterations: {DEFAULT_ITERATIONS}\n"
        f"data: {token.decode('ascii')}\n"
    )


# ---------- import ----------

def _parse_backup(text: str) -> Tuple[bytes, int, bytes]:
    """Return (salt, iterations, ciphertext)."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("CARD_COLLECTION_BACKUP"):
        raise ValueError("not a card-collection backup file")
    fields: Dict[str, str] = {}
    for ln in lines[1:]:
        key, _, value = ln.partition(":")
        if key and value:
            fields[key.strip().lower()] = value.strip()
    try:
        salt = base64.urlsafe_b64decode(fields["salt"].encode("ascii"))
        iterations = int(fields["iterations"])
        ciphertext = fields["data"].encode("ascii")
    except (KeyError, ValueError) as exc:
        raise ValueError("backup file is malformed") from exc
    return salt, iterations, ciphertext


def import_profile(db: Session, encrypted_text: str, password: str, replace: bool = True) -> Dict[str, int]:
    """Decrypt and load a backup. Returns counts of restored rows.

    ``replace=True`` wipes the existing tables first; with ``replace=False`` the
    backup is merged on top (rows just get inserted; IDs are reassigned by the DB).
    """
    if not password:
        raise ValueError("password is required")
    salt, iterations, ciphertext = _parse_backup(encrypted_text)
    key = _derive_key(password, salt, iterations)
    try:
        plaintext = Fernet(key).decrypt(ciphertext)
    except InvalidToken as exc:
        raise ValueError("wrong password or corrupted backup") from exc

    payload = json.loads(plaintext.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("backup payload is not a dict")

    if replace:
        db.query(models.PriceHistory).delete()
        db.query(models.Card).delete()
        db.query(models.SealedProduct).delete()
        db.commit()

    counts = {"cards": 0, "sealed_products": 0, "price_history": 0}

    # Map old card / sealed IDs to new ones so we can rewrite price_history rows.
    old_to_new_card: Dict[int, int] = {}
    old_to_new_sealed: Dict[int, int] = {}

    for raw in payload.get("cards") or []:
        old_id = raw.pop("id", None)
        clean = {k: raw[k] for k in _CARD_COLUMNS if k in raw}
        card = models.Card(**clean)
        db.add(card)
        db.flush()
        if old_id is not None:
            old_to_new_card[int(old_id)] = card.id
        counts["cards"] += 1

    for raw in payload.get("sealed_products") or []:
        old_id = raw.pop("id", None)
        clean = {k: raw[k] for k in _SEALED_COLUMNS if k in raw}
        sealed = models.SealedProduct(**clean)
        db.add(sealed)
        db.flush()
        if old_id is not None:
            old_to_new_sealed[int(old_id)] = sealed.id
        counts["sealed_products"] += 1

    for raw in payload.get("price_history") or []:
        item_type = raw.get("item_type")
        old_id = raw.get("item_id")
        if old_id is None:
            continue
        if item_type == "card":
            new_id = old_to_new_card.get(int(old_id))
        elif item_type == "sealed":
            new_id = old_to_new_sealed.get(int(old_id))
        else:
            continue
        if new_id is None:
            continue  # parent row didn't make it through
        ph = models.PriceHistory(
            item_type=item_type,
            item_id=new_id,
            source=raw.get("source") or "",
            price=float(raw.get("price") or 0),
        )
        db.add(ph)
        counts["price_history"] += 1

    db.commit()
    return counts
