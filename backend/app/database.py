from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, future=True)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _upgrade_sqlite_schema()


def _upgrade_sqlite_schema() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    with engine.begin() as conn:
        if "moment_interactions" in table_names:
            columns = {item["name"] for item in inspector.get_columns("moment_interactions")}
            if "actor_name" not in columns:
                conn.execute(text("ALTER TABLE moment_interactions ADD COLUMN actor_name VARCHAR DEFAULT ''"))
        if "characters" in table_names:
            columns = {item["name"] for item in inspector.get_columns("characters")}
            if "tts_voice_profile_id" not in columns:
                conn.execute(text("ALTER TABLE characters ADD COLUMN tts_voice_profile_id VARCHAR DEFAULT ''"))
            if "key_reply_threshold" not in columns:
                conn.execute(text("ALTER TABLE characters ADD COLUMN key_reply_threshold INTEGER DEFAULT 75"))
        if "proactive_events" in table_names:
            columns = {item["name"] for item in inspector.get_columns("proactive_events")}
            additions = {
                "user_id": "VARCHAR DEFAULT 'demo_user'",
                "character_id": "VARCHAR DEFAULT 'sakura'",
                "source_type": "VARCHAR DEFAULT ''",
                "source_id": "VARCHAR DEFAULT ''",
                "title": "VARCHAR DEFAULT ''",
                "text": "TEXT DEFAULT ''",
                "priority": "INTEGER DEFAULT 50",
                "status": "VARCHAR DEFAULT 'pending'",
                "dedupe_key": "VARCHAR DEFAULT ''",
                "scheduled_at": "VARCHAR DEFAULT ''",
                "expires_at": "VARCHAR DEFAULT ''",
                "payload_json": "TEXT DEFAULT '{}'",
                "delivered_at": "VARCHAR DEFAULT ''",
                "opened_at": "VARCHAR DEFAULT ''",
                "reflected_at": "VARCHAR DEFAULT ''",
                "created_at": "VARCHAR DEFAULT ''",
                "updated_at": "VARCHAR DEFAULT ''",
            }
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE proactive_events ADD COLUMN {name} {definition}"))
