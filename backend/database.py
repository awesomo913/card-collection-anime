"""SQL engine and declarative base — shared by FastAPI and Alembic."""
from sqlalchemy import create_engine
import os
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    engine = create_engine(DATABASE_URL)
else:
    SQLALCHEMY_DATABASE_URL = "sqlite:///./card_collection.db"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
