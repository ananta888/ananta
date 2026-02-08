import os
import logging
import time
import portalocker
from sqlmodel import SQLModel, create_engine, Session, select
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, IntegrityError
from agent.config import settings

# Datenbank-URL aus zentralen Einstellungen beziehen
DATABASE_URL = settings.effective_database_url

engine = create_engine(
    DATABASE_URL, 
    echo=False, 
    pool_pre_ping=True,
    pool_recycle=3600
)

def _is_in_memory_sqlite(url: str) -> bool:
    return url.startswith("sqlite:///:memory:")

def init_db():
    import agent.db_models

    # In-memory SQLite is process-local and does not require file locking.
    if _is_in_memory_sqlite(DATABASE_URL):
        SQLModel.metadata.create_all(engine)
        _ensure_schema_compat()
        ensure_default_user()
        return
    
    # Ensure data directory exists for the lock file
    os.makedirs(settings.data_dir, exist_ok=True)
    
    # Lock-Datei Pfad (im Datenverzeichnis)
    lock_file_path = os.path.join(settings.data_dir, "db_init.lock")
    
    max_retries = 5
    retry_delay = 5
    last_exception = None
    
    for i in range(max_retries):
        try:
            # Versuche exklusiven Lock zu erhalten
            with open(lock_file_path, "a+") as f:
                try:
                    portalocker.lock(f, portalocker.LOCK_EX | portalocker.LOCK_NB)
                    logging.info("Database init lock acquired.")
                    
                    try:
                        SQLModel.metadata.create_all(engine)
                        _ensure_schema_compat()
                        ensure_default_user()
                        return
                    except Exception as inner_e:
                        logging.error(f"Error during metadata creation: {inner_e}")
                        raise inner_e
                    finally:
                        # portalocker release happens automatically when file is closed, 
                        # but we want to be explicit if possible or just rely on the with block
                        pass
                except (portalocker.LockException, portalocker.AlreadyLocked):
                    logging.info(f"Database is being initialized by another process. Waiting... ({i+1}/{max_retries})")
                    time.sleep(retry_delay)
                    continue

        except OperationalError as e:
            last_exception = e
            if i < max_retries - 1:
                logging.warning(f"Database connection failed: {e}. Retrying in {retry_delay}s... ({i+1}/{max_retries})")
                time.sleep(retry_delay)
            else:
                logging.error("Max retries reached. Could not initialize database.")
                raise last_exception
        except Exception as e:
            logging.error(f"Unexpected error during init_db: {e}")
            if i < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise e

def ensure_default_user():
    from agent.db_models import UserDB
    from werkzeug.security import generate_password_hash
    
    if settings.disable_initial_admin:
        logging.info("Initial admin creation is disabled.")
        return

    with Session(engine) as session:
        # Prüfen ob bereits Benutzer existieren
        statement = select(UserDB)
        existing_user = session.exec(statement).first()
        
        if not existing_user:
            username = settings.initial_admin_user
            password = settings.initial_admin_password
            
            if not password:
                import secrets
                password = secrets.token_urlsafe(16)
                logging.warning(f"NO INITIAL PASSWORD PROVIDED. GENERATED RANDOM PASSWORD.")

            admin_user = UserDB(
                username=username,
                password_hash=generate_password_hash(password),
                role="admin"
            )
            session.add(admin_user)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                logging.info("Initial admin user creation skipped: another process created it concurrently.")
                return
            
            logging.info(f"INITIAL USER CREATED: username='{username}' (PLEASE CHANGE IMMEDIATELY)")
            # Auch auf stdout ausgeben, damit es in den Logs auffällt
            print("\n" + "="*50)
            print("INITIAL USER CREATED")
            print(f"Username: {username}")
            print(f"Password: {password}")
            print("Role:     admin")
            print("="*50 + "\n")
        else:
            logging.info(f"Database already contains users. Initial user '{settings.initial_admin_user}' not created.")

def _ensure_schema_compat() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return
    columns = {col["name"] for col in inspector.get_columns("users")}
    if "mfa_backup_codes" not in columns:
        logging.warning("DB schema missing users.mfa_backup_codes; applying compatibility migration.")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN mfa_backup_codes JSON"))

def get_session():
    with Session(engine) as session:
        yield session
