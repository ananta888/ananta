from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, scoped_session
from src.config.settings import settings
import logging

logger = logging.getLogger(__name__)

# Engine-Argumente vorbereiten
engine_kwargs = {
    "pool_pre_ping": True,
    "pool_recycle": 3600,
}

# SQLite unterstützt kein pool_size und max_overflow in der gleichen Weise wie Postgres
if not settings.database_url.startswith("sqlite"):
    engine_kwargs["pool_size"] = settings.db_pool_size
    engine_kwargs["max_overflow"] = settings.db_max_overflow

# Engine mit Connection Pooling erstellen
engine = create_engine(
    settings.database_url,
    **engine_kwargs
)

# Session-Fabrik
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Scoped Session für Thread-Sicherheit (nützlich in Flask)
db_session = scoped_session(SessionLocal)

def get_db():
    """Abhängigkeit für DB-Sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def check_db_health():
    """Prüft, ob die DB erreichbar ist."""
    try:
        # Führe ein einfaches SELECT 1 aus
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Datenbank Health-Check fehlgeschlagen: {e}")
        return False
