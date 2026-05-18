import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
import forecast_service
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
async def identify_video(
    file: UploadFile = File(...),
    game_hint: Optional[str] = None,
):
    """Identify cards visible in a short video (binder flip / pile pan / shelf).

    Server-side ffmpeg extracts up to 8 frames (1 every 2s) from the upload,
    sends them all to DeepSeek in a single multi-image call, then dedups
    candidates across frames. Returns one IdentifyResult.
    """
    mime = (file.content_type or "").lower()
    if mime not in _VIDEO_MIME_ALLOWLIST:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported video type {mime!r}. "
                f"Allowed: {sorted(_VIDEO_MIME_ALLOWLIST)}"
            ),
        )
    body = await file.read()
    if len(body) > _MAX_VIDEO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Video is {len(body)} bytes; cap is {_MAX_VIDEO_BYTES} bytes "
                f"(~{_MAX_VIDEO_BYTES // (1024*1024)} MB). Trim or compress."
            ),
        )
    client = _require_deepseek_client()
    logger.info(
        "identify/video filename=%s bytes=%s mime=%s hint=%s",
        file.filename, len(body), mime, game_hint,
    )
    return identify_service.identify_video(
        client, file.filename or "video", body, game_hint=game_hint,
    )


# ============================================================================
# /forecast — DeepSeek-powered short-term price projection
#
# Per-item, on-demand from the detail page. Server caches results for 24h
# (also invalidated automatically when a new PriceHistory row lands — see
# forecast_service._cache_key). Cost-conscious: a 100-card collection that
# the user spot-checks daily costs ~$0.20/month at deepseek-v4-pro pricing.
# ============================================================================

def _fetch_item_and_history(
    db: Session, item_type: str, item_id: int,
) -> tuple[models.Card | models.SealedProduct, list[models.PriceHistory]]:
    """Load the item + its price history. Raises 404 when missing."""
    if item_type == "card":
        item = db.query(models.Card).filter(models.Card.id == item_id).first()
    else:
        item = db.query(models.SealedProduct).filter(models.SealedProduct.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail=f"{item_type} {item_id} not found")
    history = crud.get_price_history_for_item(db, item_type, item_id)
    return item, history


@app.get("/forecast/card/{card_id}", response_model=schemas.ForecastResult)
def forecast_card(card_id: int, db: Session = Depends(get_db)):
    """Speculative price projection for a single card. Cached 24h server-side."""
    client = _require_deepseek_client()
    item, history = _fetch_item_and_history(db, "card", card_id)
    return forecast_service.forecast_item(client, "card", item, history)


@app.get("/forecast/sealed/{sealed_id}", response_model=schemas.ForecastResult)
def forecast_sealed(sealed_id: int, db: Session = Depends(get_db)):
    """Speculative price projection for a sealed product. Cached 24h server-side."""
    client = _require_deepseek_client()
    item, history = _fetch_item_and_history(db, "sealed", sealed_id)
    return forecast_service.forecast_item(client, "sealed", item, history)


# Whole-collection forecast batch. Bounded fan-out via thread pool because each
# forecast_item() call is a blocking HTTP call to DeepSeek; semaphore-style
# concurrency keeps us under their per-key rate limit (no published number,
# so 4 workers is the conservative match with IDENTIFY_WORKERS).
_FORECAST_WORKERS = int(os.environ.get("FORECAST_WORKERS", "4"))
_MAX_FORECAST_BATCH = 500
_AGGREGATE_HORIZONS = (7, 30, 90)
_AGGREGATE_CONFIDENCE_FLOOR = 0.3


def _compute_batch_aggregate(
    rows: List[schemas.BatchForecastResultRow],
) -> List[schemas.AggregateHorizon]:
    """Portfolio roll-up across the batch.

    For each horizon (7/30/90d):
      - Items with a forecast at ≥ floor confidence contribute qty × horizon.{low,target,high}.
      - Items below floor / missing forecast / errored contribute qty × current_price
        in all three columns (no projection trusted) and increment items_skipped.
      - confidence_weighted_target = Σ(qty × target × confidence) / Σ(qty × confidence)
        across included items only.
    """
    out: list[schemas.AggregateHorizon] = []
    for days in _AGGREGATE_HORIZONS:
        cur_total = 0.0
        proj_low = 0.0
        proj_tgt = 0.0
        proj_hi = 0.0
        weighted_num = 0.0
        weighted_den = 0.0
        included = 0
        skipped = 0
        for row in rows:
            qty = row.qty or 0
            current = row.current_price or 0.0
            cur_total += current * qty
            horizon = None
            if row.forecast is not None and row.error is None:
                horizon = next(
                    (h for h in row.forecast.horizons if h.days == days), None,
                )
            if horizon is None or horizon.confidence < _AGGREGATE_CONFIDENCE_FLOOR:
                proj_low += current * qty
                proj_tgt += current * qty
                proj_hi += current * qty
                skipped += 1
                continue
            proj_low += horizon.low * qty
            proj_tgt += horizon.target * qty
            proj_hi += horizon.high * qty
            weighted_num += horizon.target * qty * horizon.confidence
            weighted_den += qty * horizon.confidence
            included += 1
        weighted = (weighted_num / weighted_den) if weighted_den > 0 else 0.0
        out.append(schemas.AggregateHorizon(
            days=days,
            current_total=round(cur_total, 2),
            projected_low=round(proj_low, 2),
            projected_target=round(proj_tgt, 2),
            projected_high=round(proj_hi, 2),
            confidence_weighted_target=round(weighted, 2),
            items_included=included,
            items_skipped=skipped,
        ))
    return out


@app.post("/forecast/batch", response_model=schemas.BatchForecastResponse)
def forecast_batch(
    req: schemas.BatchForecastRequest,
    db: Session = Depends(get_db),
):
    """Forecast many items in one call. Returns per-item rows + portfolio roll-up.

    Per-item failures don't abort the batch — each row carries its own ``error``
    field. Cache hits cost nothing (the existing ``_cache`` key fires before
    any DeepSeek call), so re-running while history is unchanged is ~free.
    """
    if not req.items:
        return schemas.BatchForecastResponse(
            results=[], aggregate=[], duration_seconds=0.0,
            cache_hits=0, cache_misses=0, model="(none)",
        )
    if len(req.items) > _MAX_FORECAST_BATCH:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Batch size {len(req.items)} exceeds limit of "
                f"{_MAX_FORECAST_BATCH}. Split into smaller requests."
            ),
        )
    client = _require_deepseek_client()
    started = time.monotonic()

    # Pre-load items + history serially (DB-bound + cheap). Doing this on the
    # request thread avoids opening N parallel DB sessions for the same Pi.
    prepared: list[tuple[int, str, object, list, Optional[str]]] = []
    for idx, it in enumerate(req.items):
        try:
            item, history = _fetch_item_and_history(db, it.type, it.id)
            prepared.append((idx, it.type, item, history, None))
        except HTTPException as exc:
            prepared.append((idx, it.type, None, [], str(exc.detail)))

    results: list[Optional[schemas.BatchForecastResultRow]] = [None] * len(prepared)

    def _run_one(prep_tuple):
        idx, item_type, item, history, prep_err = prep_tuple
        # Capture the original id so 404'd items still show up in results.
        original_id = req.items[idx].id
        if prep_err is not None:
            return idx, schemas.BatchForecastResultRow(
                type=item_type, id=original_id, name="(unknown)", qty=0,
                current_price=None, forecast=None, error=prep_err,
            )
        try:
            forecast = forecast_service.forecast_item(
                client, item_type, item, history,
            )
            return idx, schemas.BatchForecastResultRow(
                type=item_type, id=item.id, name=item.name,
                qty=int(getattr(item, "quantity", 1) or 1),
                current_price=item.current_price,
                forecast=forecast, error=None,
            )
        except Exception as exc:  # noqa: BLE001 — batch row tolerates any error
            logger.warning(
                "forecast/batch row failed type=%s id=%s: %s",
                item_type, original_id, exc,
            )
            return idx, schemas.BatchForecastResultRow(
                type=item_type, id=item.id, name=item.name,
                qty=int(getattr(item, "quantity", 1) or 1),
                current_price=item.current_price,
                forecast=None, error=str(exc),
            )

    cache_hits = 0
    cache_misses = 0
    last_model = "(none)"
    with ThreadPoolExecutor(max_workers=_FORECAST_WORKERS) as pool:
        for fut in as_completed([pool.submit(_run_one, p) for p in prepared]):
            idx, row = fut.result()
            results[idx] = row
            if row.forecast is not None:
                if row.forecast.cached:
                    cache_hits += 1
                else:
                    cache_misses += 1
                last_model = row.forecast.model

    # Fill any None slots defensively (shouldn't happen but keeps the type honest).
    final_rows: list[schemas.BatchForecastResultRow] = [
        r if r is not None else schemas.BatchForecastResultRow(
            type="card", id=req.items[i].id, error="internal: row never resolved",
        )
        for i, r in enumerate(results)
    ]
    aggregate = _compute_batch_aggregate(final_rows)
    duration = time.monotonic() - started
    logger.info(
        "forecast/batch n=%s duration=%.2fs hits=%s misses=%s",
        len(final_rows), duration, cache_hits, cache_misses,
    )
    return schemas.BatchForecastResponse(
        results=final_rows, aggregate=aggregate,
        duration_seconds=round(duration, 3),
        cache_hits=cache_hits, cache_misses=cache_misses,
        model=last_model,
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
