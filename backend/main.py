import logging
import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, Depends, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
import crud, models, schemas
from database import SessionLocal, engine
from pathlib import Path
from price_service import update_all_prices
from providers import catalog as catalog_module
from providers.deepseek import DeepSeekVision
import identify_service
import profile_backup
import status as status_module
from scheduler import start_scheduler
import uvicorn
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


def _self_heal_schema() -> None:
    """Idempotent ALTER TABLE for any model column missing on the live DB.

    Why this exists: ``Base.metadata.create_all`` only creates missing TABLES,
    not missing COLUMNS. When models gain new optional columns (e.g. the
    ``tcgplayer_product_id`` added in migration b2c3d4e5f6a7), an existing
    SQLite DB needs an ALTER TABLE. Alembic does this properly but is easy
    to forget on hand-rolled deploys; this is a belt-and-suspenders fallback
    so a schema bump never strands a running Pi.

    Walks every mapped model, compares to actual DB columns via SQLAlchemy
    inspector, and ALTERs in any missing nullable column. Safe to call on
    every boot — no-op when schema is current.
    """
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for mapper in models.Base.registry.mappers:
            table = mapper.local_table
            if table.name not in existing_tables:
                continue  # create_all() will handle it next.
            live_cols = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in live_cols:
                    continue
                # Only auto-add columns the model marked nullable. Adding
                # a NOT NULL column without a default would brick existing rows.
                if not col.nullable:
                    msg = (
                        f"Manual migration needed: column {table.name}.{col.name} "
                        f"is NOT NULL without a default. Self-heal cannot add it."
                    )
                    logger.warning(msg)
                    status_module.record_schema_warning(msg)
                    continue
                try:
                    col_type = col.type.compile(dialect=engine.dialect)
                    stmt = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'
                    logger.info("schema self-heal: %s", stmt)
                    conn.execute(text(stmt))
                except Exception as exc:  # noqa: BLE001 — log + surface, never crash boot
                    msg = f"Schema self-heal failed for {table.name}.{col.name}: {exc}"
                    logger.error(msg)
                    status_module.record_schema_warning(msg)


def _backfill_known_columns() -> None:
    """One-shot column-specific backfill after self-heal ALTER TABLE.

    When a new nullable column lands, existing rows get NULL by default —
    SQLite's ALTER TABLE ADD COLUMN doesn't apply ``server_default`` to the
    rows that pre-date the column. For a handful of columns that have a
    sensible historical fallback (created_at from last_updated, acquired_price
    from current_price), we backfill explicitly so the UI doesn't show empty
    "Added on" or "Initial price" fields for cards that pre-date the feature.

    Per-column rule:
    - ``created_at``  → COALESCE with ``last_updated`` (best approximation).
    - ``acquired_price`` → COALESCE with ``current_price`` (best approximation).

    All updates are idempotent: WHERE clause filters to NULL-only rows so a
    re-run is a no-op.
    """
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    targets = [
        # (table, column, source-column-to-copy-from)
        ("cards", "created_at", "last_updated"),
        ("sealed_products", "created_at", "last_updated"),
        ("cards", "acquired_price", "current_price"),
        ("sealed_products", "acquired_price", "current_price"),
    ]
    with engine.begin() as conn:
        for table, col, source in targets:
            if table not in existing_tables:
                continue
            live_cols = {c["name"] for c in insp.get_columns(table)}
            if col not in live_cols or source not in live_cols:
                continue
            try:
                # Quote identifiers for safety; values are not user-supplied.
                stmt = (
                    f'UPDATE "{table}" SET "{col}" = "{source}" '
                    f'WHERE "{col}" IS NULL'
                )
                result = conn.execute(text(stmt))
                if result.rowcount:
                    logger.info(
                        "backfilled %s.%s from %s for %s rows",
                        table, col, source, result.rowcount,
                    )
            except Exception as exc:  # noqa: BLE001 — backfill is best-effort
                logger.error("Backfill failed for %s.%s: %s", table, col, exc)


_self_heal_schema()
_backfill_known_columns()
models.Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    status_module.install_ring_handler()
    # Skip the background price scheduler in test mode so pytest stays deterministic.
    if not os.environ.get("DISABLE_SCHEDULER"):
        start_scheduler()
    yield


app = FastAPI(title="Card Collection API", lifespan=lifespan)

# CORS for local frontend + any extra origins via env (comma-separated).
origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
extra = os.environ.get("CORS_ORIGINS", "")
if extra:
    origins.extend(o.strip() for o in extra.split(",") if o.strip())
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/api")
def read_root():
    return {"message": "Card Collection API"}

# Card endpoints
@app.post("/cards/", response_model=schemas.Card)
def create_card(card: schemas.CardCreate, db: Session = Depends(get_db)):
    return crud.create_card(db=db, card=card)

@app.get("/cards/", response_model=list[schemas.Card])
def read_cards(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    cards = crud.get_cards(db, skip=skip, limit=limit)
    return cards

@app.get("/cards/{card_id}", response_model=schemas.Card)
def read_card(card_id: int, db: Session = Depends(get_db)):
    db_card = crud.get_card(db, card_id=card_id)
    if db_card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return db_card

@app.put("/cards/{card_id}", response_model=schemas.Card)
def update_card(card_id: int, card: schemas.CardUpdate, db: Session = Depends(get_db)):
    db_card = crud.update_card(db=db, card_id=card_id, card=card)
    if db_card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return db_card

@app.delete("/cards/{card_id}")
def delete_card(card_id: int, db: Session = Depends(get_db)):
    db_card = crud.delete_card(db=db, card_id=card_id)
    if db_card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return {"message": "Card deleted"}

# Sealed product endpoints
@app.post("/sealed/", response_model=schemas.SealedProduct)
def create_sealed_product(sealed: schemas.SealedProductCreate, db: Session = Depends(get_db)):
    return crud.create_sealed_product(db=db, sealed=sealed)

@app.get("/sealed/", response_model=list[schemas.SealedProduct])
def read_sealed_products(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    sealed = crud.get_sealed_products(db, skip=skip, limit=limit)
    return sealed

@app.get("/sealed/{sealed_id}", response_model=schemas.SealedProduct)
def read_sealed_product(sealed_id: int, db: Session = Depends(get_db)):
    db_sealed = crud.get_sealed_product(db, sealed_id=sealed_id)
    if db_sealed is None:
        raise HTTPException(status_code=404, detail="Sealed product not found")
    return db_sealed

@app.put("/sealed/{sealed_id}", response_model=schemas.SealedProduct)
def update_sealed_product(sealed_id: int, sealed: schemas.SealedProductUpdate, db: Session = Depends(get_db)):
    db_sealed = crud.update_sealed_product(db=db, sealed_id=sealed_id, sealed=sealed)
    if db_sealed is None:
        raise HTTPException(status_code=404, detail="Sealed product not found")
    return db_sealed

@app.delete("/sealed/{sealed_id}")
def delete_sealed_product(sealed_id: int, db: Session = Depends(get_db)):
    db_sealed = crud.delete_sealed_product(db=db, sealed_id=sealed_id)
    if db_sealed is None:
        raise HTTPException(status_code=404, detail="Sealed product not found")
    return {"message": "Sealed product deleted"}

# Collection value endpoint
@app.get("/collection/value")
def get_collection_value(db: Session = Depends(get_db)):
    total_value = crud.get_collection_value(db=db)
    return {"total_value": total_value}

# Price update endpoint (for manual trigger)
@app.post("/prices/update")
def trigger_price_update():
    update_all_prices()
    return {"message": "Price update triggered"}

@app.get("/snapshot")
def price_snapshot(db: Session = Depends(get_db)):
    """
    Return a snapshot of estimated value by price source across the collection.
    """
    return crud.price_snapshot(db=db)

@app.get("/price-history/{item_type}/{item_id}")
def price_history(item_type: str, item_id: int, db: Session = Depends(get_db)):
    """Return historical prices for a given item from all sources."""
    hist = crud.get_price_history_for_item(db=db, item_type=item_type, item_id=item_id)
    return [
        {"source": h.source, "price": h.price, "timestamp": h.timestamp}
        for h in hist
    ]


@app.get("/health")
def health():
    """Lightweight liveness probe — used by load balancers and the status UI."""
    return {"ok": True}


@app.get("/status")
def status_view(db: Session = Depends(get_db)):
    """Operational snapshot: uptime, system metrics, scheduler health, DB counts."""
    cards_count = db.query(models.Card).count()
    sealed_count = db.query(models.SealedProduct).count()
    history_count = db.query(models.PriceHistory).count()
    total_value = crud.get_collection_value(db=db)
    return {
        **status_module.overview(),
        "system": status_module.system_snapshot(),
        "database": {
            "cards": cards_count,
            "sealed_products": sealed_count,
            "price_history_rows": history_count,
            "total_value": total_value,
        },
    }


@app.get("/status/logs")
def status_logs(limit: int = 100, level: str | None = None):
    """Last N log records held in the in-memory ring buffer."""
    return status_module.recent_logs(limit=min(max(1, limit), 500), level=level)


@app.post("/profile/export", response_class=PlainTextResponse)
def profile_export(req: schemas.BackupExportRequest, db: Session = Depends(get_db)):
    """Return an encrypted text blob containing the entire collection."""
    if not req.password:
        raise HTTPException(status_code=400, detail="password is required")
    try:
        return profile_backup.export_profile(db, req.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/profile/import")
def profile_import(req: schemas.BackupImportRequest, db: Session = Depends(get_db)):
    """Decrypt + restore a previously exported backup. Wrong password -> 400."""
    try:
        counts = profile_backup.import_profile(db, req.encrypted, req.password, replace=req.replace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"restored": counts}


@app.get("/catalog/resolve", response_model=schemas.CatalogResult)
def catalog_resolve(url: str):
    """Resolve a Scryfall / TCGplayer / PokemonTCG / YGOPRODeck URL to a single
    catalog entry the frontend can pin to a new card just like a search pick."""
    if not url or not url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    result = catalog_module.resolve_url(url.strip())
    if not result:
        raise HTTPException(status_code=404, detail="Could not resolve that URL")
    return result


# ============================================================================
# /identify — DeepSeek multimodal card identification
#
# Three endpoints share one backend client + service module:
#   POST /identify/image — single image, JSON response with up to 3 candidates
#   POST /identify/batch — many images, partial-failure tolerant
#   POST /identify/video — Phase 3: ffmpeg frame extraction + multi-image call
#
# Security gates (matches the plan's Phase 1 checklist):
# - Key only via DEEPSEEK_API_KEY env var. Endpoint 503s when missing.
# - Per-file size cap enforced before reading body fully into memory.
# - MIME allowlist. Anything else returns 415.
# - Log filename + size + mime + duration. NEVER the binary.
# ============================================================================

_MAX_IMAGE_BYTES = 10 * 1024 * 1024     # 10 MB per image
_MAX_VIDEO_BYTES = 50 * 1024 * 1024     # 50 MB per video (Phase 3)
_MAX_BATCH_ITEMS = 32                   # one upload batch ceiling
_IMAGE_MIME_ALLOWLIST = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic",
    "image/heif",
}
_VIDEO_MIME_ALLOWLIST = {"video/mp4", "video/quicktime", "video/webm"}


def _require_deepseek_client() -> DeepSeekVision:
    """Construct + sanity-check the DeepSeek client; raise 503 when missing."""
    client = DeepSeekVision()
    if not client.is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Image identification is unavailable: DEEPSEEK_API_KEY env var "
                "is not set on this Pi. Set it and restart uvicorn to enable."
            ),
        )
    return client


async def _read_image_upload(file: UploadFile) -> tuple[bytes, str]:
    """Validate + read one image upload into memory; raise HTTPException on bad input."""
    mime = (file.content_type or "").lower()
    if mime not in _IMAGE_MIME_ALLOWLIST:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported image type {mime!r}. "
                f"Allowed: {sorted(_IMAGE_MIME_ALLOWLIST)}"
            ),
        )
    body = await file.read()
    if len(body) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Image is {len(body)} bytes; cap is {_MAX_IMAGE_BYTES} bytes "
                f"(~{_MAX_IMAGE_BYTES // (1024*1024)} MB). Resize and retry."
            ),
        )
    return body, mime


@app.post("/identify/image", response_model=schemas.IdentifyResult)
async def identify_image(
    file: UploadFile = File(...),
    game_hint: Optional[str] = None,
):
    """Identify one card image. Returns up to 3 ranked candidates.

    ``game_hint`` is optional; pass "magic"/"pokemon"/"yugioh" when the caller
    already knows the game (e.g., the user is on the Add Card page with the
    game dropdown set). The model is told to bias toward that game unless
    the visual evidence clearly contradicts it.
    """
    client = _require_deepseek_client()
    body, mime = await _read_image_upload(file)
    logger.info(
        "identify/image filename=%s bytes=%s mime=%s hint=%s",
        file.filename, len(body), mime, game_hint,
    )
    return identify_service.identify_single(
        client, file.filename or "image", body, mime, game_hint=game_hint,
    )


@app.post("/identify/batch", response_model=schemas.IdentifyBatchResponse)
async def identify_batch(
    files: List[UploadFile] = File(...),
):
    """Identify many card images in parallel. Per-item failures don't abort.

    Ideal for blasting through a binder: drop 30 photos, get back a review
    queue with each result tagged ok/error. Total wall-clock duration is
    returned so the UI can spot DeepSeek rate-limit slowdowns.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > _MAX_BATCH_ITEMS:
        raise HTTPException(
            status_code=413,
            detail=f"Batch size {len(files)} exceeds limit of {_MAX_BATCH_ITEMS}",
        )
    client = _require_deepseek_client()
    items: list[tuple[str, bytes, str]] = []
    for f in files:
        body, mime = await _read_image_upload(f)
        items.append((f.filename or "image", body, mime))
    logger.info(
        "identify/batch n=%s total_bytes=%s",
        len(items), sum(len(b) for _, b, _ in items),
    )
    return identify_service.identify_batch(client, items)


@app.post("/identify/video", response_model=schemas.IdentifyResult)
async def identify_video(file: UploadFile = File(...)):
    """Phase 3 placeholder. Returns 501 until ffmpeg frame extraction lands."""
    raise HTTPException(
        status_code=501,
        detail=(
            "Video identification is not implemented yet (Phase 3). "
            "Upload individual frames via /identify/image for now."
        ),
    )


@app.get("/catalog/search", response_model=list[schemas.CatalogResult])
def catalog_search(q: str, game: str, limit: int = 12, sealed: bool = False):
    """Live search the public catalog for the chosen game.

    Returns normalized rows (image, set, TCGplayer price) the frontend can pin to
    a new card so future refreshes hit the exact catalog ID instead of guessing
    by name. Pass ``sealed=true`` to constrain Magic searches to sealed product;
    Pokemon and Yu-Gi-Oh return [] in sealed mode (their public APIs only carry
    single cards — use the URL paste flow for those instead).
    """
    if not q or len(q.strip()) < 2:
        return []
    if game.lower() not in {"magic", "pokemon", "yugioh"}:
        raise HTTPException(status_code=400, detail="game must be magic, pokemon, or yugioh")
    return catalog_module.search(
        q.strip(), game.lower(), limit=min(max(1, limit), 24), sealed=sealed
    )

# Single-port deploy mode: when ../frontend/build exists (e.g. on a Pi after
# `npm run build`), serve the static UI from the same FastAPI process. Mounted
# last so explicit API routes above always win.
_FRONTEND_BUILD = Path(__file__).resolve().parent.parent / "frontend" / "build"
if _FRONTEND_BUILD.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_BUILD), html=True), name="ui")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
