from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

DB_CONNECT_TIMEOUT_SECONDS = 3


def _engine_options(database_url: str) -> dict:
    if not database_url.startswith("postgresql"):
        return {}  # SQLite in tests
    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 5,
        "connect_args": {"connect_timeout": DB_CONNECT_TIMEOUT_SECONDS},
    }


engine = create_engine(settings.database_url, **_engine_options(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def database_is_reachable() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
