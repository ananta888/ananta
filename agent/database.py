import logging
import os
import time

import portalocker
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.config import settings

# Datenbank-URL aus zentralen Einstellungen beziehen
DATABASE_URL = settings.effective_database_url

connect_args = {}
engine_kwargs = {
    "echo": False,
    "pool_pre_ping": True,
    "pool_recycle": 3600,
    "connect_args": connect_args,
}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False
    if DATABASE_URL == "sqlite:///:memory:":
        # Share the in-memory test database across repository sessions.
        engine_kwargs["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, **engine_kwargs)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def _is_in_memory_sqlite(url: str) -> bool:
    return url.startswith("sqlite:///:memory:")


def _is_postgresql(url: str) -> bool:
    return url.startswith("postgresql")


def _wait_for_required_schema(max_retries: int, retry_delay: int) -> None:
    required_tables = {"agents", "tasks", "users"}
    last_exception = None

    for i in range(max_retries):
        try:
            inspector = inspect(engine)
            existing_tables = set(inspector.get_table_names())
            missing_tables = required_tables - existing_tables
            if not missing_tables:
                return
            logging.info(
                "Database schema not ready yet. Missing tables: %s (%s/%s)",
                ", ".join(sorted(missing_tables)),
                i + 1,
                max_retries,
            )
        except OperationalError as e:
            last_exception = e
            logging.warning(
                "Database connection failed while waiting for schema: %s. Retrying in %ss... (%s/%s)",
                e,
                retry_delay,
                i + 1,
                max_retries,
            )
        time.sleep(retry_delay)

    if last_exception is not None:
        raise last_exception
    raise RuntimeError("Required PostgreSQL schema was not ready in time.")


def init_db():

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

    if _is_postgresql(DATABASE_URL):
        _wait_for_required_schema(max_retries=max_retries, retry_delay=retry_delay)
        ensure_default_user()
        return

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
                        # portalocker release happens automatically when file is closed
                        logging.debug("Database initialization lock released")
                except (portalocker.LockException, portalocker.AlreadyLocked):
                    logging.info(
                        f"Database is being initialized by another process. Waiting... ({i + 1}/{max_retries})"
                    )
                    time.sleep(retry_delay)
                    continue

        except OperationalError as e:
            last_exception = e
            if i < max_retries - 1:
                logging.warning(
                    f"Database connection failed: {e}. Retrying in {retry_delay}s... ({i + 1}/{max_retries})"
                )
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
    from werkzeug.security import generate_password_hash

    from agent.db_models import UserDB

    if settings.disable_initial_admin:
        logging.info("Initial admin creation is disabled.")
        return

    # In shared PostgreSQL deployments multiple agents can start concurrently.
    # Restrict initial admin bootstrap to the hub process to avoid duplicate inserts.
    if settings.effective_database_url.startswith("postgresql") and settings.role != "hub":
        logging.info(
            "Initial admin creation skipped on non-hub role '%s' for shared PostgreSQL setup.",
            settings.role,
        )
        return

    with Session(engine) as session:
        # Prüfen ob bereits Benutzer existieren
        statement = select(UserDB)
        existing_user = session.exec(statement).first()

        if not existing_user:
            username = settings.initial_admin_user
            password = settings.initial_admin_password

            is_generated = False
            if not password:
                import secrets

                password = secrets.token_urlsafe(16)
                is_generated = True
                logging.warning("NO INITIAL PASSWORD PROVIDED. GENERATED RANDOM PASSWORD.")

            admin_user = UserDB(username=username, password_hash=generate_password_hash(password), role="admin")
            session.add(admin_user)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                logging.info("Initial admin user creation skipped: another process created it concurrently.")
                return

            logging.info(f"INITIAL USER CREATED: username='{username}' (PLEASE CHANGE IMMEDIATELY)")
            # Sichtbarer Hinweis ohne Klartext-Passwort in Logs/Stdout.
            print("\n" + "=" * 50)
            print("INITIAL USER CREATED")
            print(f"Username: {username}")
            if is_generated:
                print(f"Password: {password}  <-- COPY THIS NOW!")
            else:
                print("Password: [hidden]")
            print("Action:   Set a secure password immediately after first login.")
            print("Role:     admin")
            print("=" * 50 + "\n")
        else:
            logging.info(f"Database already contains users. Initial user '{settings.initial_admin_user}' not created.")


def _ensure_schema_compat() -> None:
    # Schema-Migrationen laufen exklusiv ueber Alembic.
    # Daten-Backfills werden ueber explizite Wartungskommandos ausgefuehrt.
    return


def get_session():
    with Session(engine) as session:
        yield session
