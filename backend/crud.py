from sqlalchemy.orm import Session
import models, schemas
from typing import Optional
from price_service import (
    fetch_card_price,
    fetch_card_prices_all_sources,
    fetch_sealed_price,
    fetch_sealed_prices_all_sources,
)
from datetime import datetime, timedelta
from sqlalchemy import func

def get_card(db: Session, card_id: int):
    return db.query(models.Card).filter(models.Card.id == card_id).first()

def get_cards(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Card).offset(skip).limit(limit).all()

def create_card(db: Session, card: schemas.CardCreate):
    db_card = models.Card(**card.model_dump())
    sources = fetch_card_prices_all_sources(
        db_card.name, db_card.set_name, db_card.game, db_card.is_foil,
        external_source=db_card.external_source,
        external_id=db_card.external_id,
    )
    db_card.price_sources = sources or None
    db_card.current_price = (
        round(sum(sources.values()) / len(sources), 2) if sources else None
    )
    db.add(db_card)
    db.commit()
    db.refresh(db_card)
    return db_card

def update_card(db: Session, card_id: int, card: schemas.CardUpdate):
    db_card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if db_card:
        for key, value in card.model_dump(exclude_unset=True).items():
            setattr(db_card, key, value)
        # Re-fetch price when any pricing-relevant field changed.
        relevant = {"name", "set_name", "game", "is_foil"}
        if relevant & set(card.model_dump(exclude_unset=True).keys()):
            price = fetch_card_price(
                db_card.name, db_card.set_name, db_card.game, db_card.is_foil,
                external_source=db_card.external_source,
                external_id=db_card.external_id,
            )
            db_card.current_price = price
        db.commit()
        db.refresh(db_card)
    return db_card

def delete_card(db: Session, card_id: int):
    db_card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if db_card:
        db.delete(db_card)
        db.commit()
    return db_card

def get_sealed_product(db: Session, sealed_id: int):
    return db.query(models.SealedProduct).filter(models.SealedProduct.id == sealed_id).first()

def get_sealed_products(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.SealedProduct).offset(skip).limit(limit).all()

def create_sealed_product(db: Session, sealed: schemas.SealedProductCreate):
    db_sealed = models.SealedProduct(**sealed.model_dump())
    # Pinned to a catalog entry? Try the catalog first for an authoritative price.
    sources: dict = {}
    if db_sealed.external_source and db_sealed.external_id:
        from providers import catalog as catalog_module
        catalog_price = catalog_module.fetch_tcgplayer_price(
            db_sealed.external_source, db_sealed.external_id, is_foil=False,
            set_name=db_sealed.set_name,
        )
        if catalog_price is not None:
            sources["TCGPlayer"] = round(float(catalog_price), 2)
    if not sources:
        sources = fetch_sealed_prices_all_sources(
            db_sealed.name, db_sealed.set_name, db_sealed.product_type, db_sealed.game,
        )
    db_sealed.price_sources = sources or None
    db_sealed.current_price = (
        round(sum(sources.values()) / len(sources), 2) if sources else None
    )
    db.add(db_sealed)
    db.commit()
    db.refresh(db_sealed)
    return db_sealed

def update_sealed_product(db: Session, sealed_id: int, sealed: schemas.SealedProductUpdate):
    db_sealed = db.query(models.SealedProduct).filter(models.SealedProduct.id == sealed_id).first()
    if db_sealed:
        for key, value in sealed.model_dump(exclude_unset=True).items():
            setattr(db_sealed, key, value)
        # Update price if any relevant field changed
        if any(key in ['name', 'set_name', 'product_type', 'game'] for key in sealed.model_dump(exclude_unset=True)):
            price = fetch_sealed_price(db_sealed.name, db_sealed.set_name, db_sealed.product_type, db_sealed.game)
            db_sealed.current_price = price
        db.commit()
        db.refresh(db_sealed)
    return db_sealed

def delete_sealed_product(db: Session, sealed_id: int):
    db_sealed = db.query(models.SealedProduct).filter(models.SealedProduct.id == sealed_id).first()
    if db_sealed:
        db.delete(db_sealed)
        db.commit()
    return db_sealed

def get_collection_value(db: Session):
    # Calculate total value of cards
    cards = db.query(models.Card).all()
    cards_value = sum(card.current_price * card.quantity for card in cards if card.current_price is not None)
    
    # Calculate total value of sealed products
    sealed_items = db.query(models.SealedProduct).all()
    sealed_value = sum(item.current_price * item.quantity for item in sealed_items if item.current_price is not None)
    
    return cards_value + sealed_value

def get_price_history_for_item(db: Session, item_type: str, item_id: int):
    return (
        db.query(models.PriceHistory)
        .filter(models.PriceHistory.item_type == item_type, models.PriceHistory.item_id == item_id)
        .order_by(models.PriceHistory.timestamp)
        .all()
    )

def log_price_history(db: Session, item_type: str, item_id: int, source: str, price: float, ts: Optional[datetime] = None):
    # Log a single price snapshot for an item from a given source
    price_timestamp = ts or datetime.utcnow()
    history = models.PriceHistory(
        item_type=item_type,
        item_id=item_id,
        source=source,
        price=price,
        timestamp=price_timestamp,
    )
    db.add(history)
    db.commit()
    db.refresh(history)
    return history

def price_snapshot(db: Session):
    """Aggregate per-source hypothetical values and one canonical collection total."""
    snapshot_by_source = {}
    # Cards — sum marginal value per marketplace source (quantity-weighted).
    cards = db.query(models.Card).all()
    for c in cards:
        if getattr(c, "price_sources", None) and c.price_sources:
            for src, price in (c.price_sources or {}).items():
                value = price * (c.quantity or 1)
                snapshot_by_source[src] = snapshot_by_source.get(src, 0.0) + value
    # Sealed — same semantics
    sealed_items = db.query(models.SealedProduct).all()
    for s in sealed_items:
        if getattr(s, "price_sources", None) and s.price_sources:
            for src, price in (s.price_sources or {}).items():
                value = price * (s.quantity or 1)
                snapshot_by_source[src] = snapshot_by_source.get(src, 0.0) + value
    # Single total comparable to Dashboard /collection/value — uses averaged current_price, not summed across sources
    total_value = get_collection_value(db)
    # Build a 24-hour price history by source (if available)
    history = []
    try:
        since = datetime.utcnow() - timedelta(hours=24)
        hist_rows = (
            db.query(
                models.PriceHistory.timestamp,
                models.PriceHistory.source,
                func.sum(models.PriceHistory.price).label('sum_price'),
            )
            .filter(models.PriceHistory.timestamp >= since)
            .group_by(models.PriceHistory.timestamp, models.PriceHistory.source)
            .order_by(models.PriceHistory.timestamp)
            .all()
        )
        # Build a mapping by timestamp -> {source: sum_price}
        grouped = {}
        for ts, src, sum_price in hist_rows:
            ts_key = ts.isoformat() if hasattr(ts, 'isoformat') else str(ts)
            grouped.setdefault(ts_key, {})[src] = float(sum_price)
        # Convert to a list of {timestamp, by_source}
        for ts_key in sorted(grouped.keys()):
            history.append({"timestamp": ts_key, "by_source": grouped[ts_key]})
    except Exception:
        # If anything fails, keep history as empty
        history = []

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "by_source": snapshot_by_source,
        "history": history,
        "total_value": total_value
    }
