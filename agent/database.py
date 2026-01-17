import os
import logging
import time
from sqlmodel import SQLModel, create_engine, Session, select
from sqlalchemy.exc import OperationalError
from agent.config import settings

# Datenbank-URL aus Umgebungsvariable oder Standard (SQLite als Fallback für Tests/lokal)
# Für Produktion wird POSTGRES_URL erwartet.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    # Fallback auf SQLite im data_dir
    db_path = os.path.join(settings.data_dir, "ananta.db")
    DATABASE_URL = f"sqlite:///{db_path}"

engine = create_engine(
    DATABASE_URL, 
    echo=False, 
    pool_pre_ping=True,
    pool_recycle=3600
)

def init_db():
    import agent.db_models
    
    max_retries = 5
    retry_delay = 5
    last_exception = None
    
    for i in range(max_retries):
        try:
            SQLModel.metadata.create_all(engine)
            ensure_default_user()
            return
        except OperationalError as e:
            last_exception = e
            if i < max_retries - 1:
                logging.warning(f"Database connection failed: {e}. Retrying in {retry_delay}s... ({i+1}/{max_retries})")
                time.sleep(retry_delay)
            else:
                logging.error("Max retries reached. Could not initialize database.")
                raise last_exception

def ensure_default_user():
    from agent.db_models import UserDB
    from werkzeug.security import generate_password_hash
    
    with Session(engine) as session:
        # Prüfen ob bereits Benutzer existieren
        statement = select(UserDB)
        existing_user = session.exec(statement).first()
        
        if not existing_user:
            admin_user = UserDB(
                username="admin",
                password_hash=generate_password_hash("admin"),
                role="admin"
            )
            session.add(admin_user)
            session.commit()
            logging.info("INITIAL USER CREATED: username='admin', password='admin' (PLEASE CHANGE IMMEDIATELY)")
            # Auch auf stdout ausgeben, damit es in den Logs auffällt
            print("\n" + "="*50)
            print("INITIAL USER CREATED")
            print("Username: admin")
            print("Password: admin")
            print("Role:     admin")
            print("="*50 + "\n")
        else:
            logging.info(f"Database already contains users. Initial user 'admin' not created.")

def get_session():
    with Session(engine) as session:
        yield session
