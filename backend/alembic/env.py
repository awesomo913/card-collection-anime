"""Alembic environment — uses same engine and metadata as FastAPI."""
from logging.config import fileConfig
import sys
from pathlib import Path

from alembic import context

# Ensure imports resolve like `python -m pytest` / `uvicorn main:app`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import engine  # noqa: E402
from models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Offline migrations (requires sqlalchemy.url set in alembic.ini when used standalone)."""
    url = context.config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("Set sqlalchemy.url in alembic.ini for offline mode.")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
