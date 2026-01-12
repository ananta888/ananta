from sqlmodel import SQLModel, create_engine, Session
from agent.config import settings
import os

# Datenbank-URL aus Umgebungsvariable oder Standard (SQLite als Fallback für Tests/lokal)
# Für Produktion wird POSTGRES_URL erwartet.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    # Fallback auf SQLite im data_dir
    db_path = os.path.join(settings.data_dir, "ananta.db")
    DATABASE_URL = f"sqlite:///{db_path}"

engine = create_engine(DATABASE_URL, echo=False)

def init_db():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
