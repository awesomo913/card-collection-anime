import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import crud, models, schemas
from database import SessionLocal, engine
from price_service import update_all_prices
from providers import catalog as catalog_module
import profile_backup
from scheduler import start_scheduler
import uvicorn
from fastapi.responses import PlainTextResponse

models.Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
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

@app.get("/")
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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
