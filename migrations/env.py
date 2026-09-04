import os
import sys
import time
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.exc import OperationalError

# Projekt-Root zum Pfad hinzufügen
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..")))

# Importiere SQLModel und Modelle
from sqlmodel import SQLModel

from agent.config import settings
from agent.database import DATABASE_URL
from agent.db_models import *  # noqa: F403  # Damit die Metadaten gefüllt sind


def backup_sqlite_db(database_url: str):
    """Erstellt ein Backup der SQLite-Datenbank vor der Migration."""
    if not database_url.startswith("sqlite:///"):
        return

    db_path = database_url.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return

    backup_dir = os.path.join(settings.data_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(backup_dir, f"pre_migration_{timestamp}.db")

    try:
        import shutil

        shutil.copy2(db_path, backup_path)
        print(f"Database backup created: {backup_path}")
    except Exception as e:
        print(f"Failed to create database backup: {e}")


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata = mymodel.Base.metadata
target_metadata = SQLModel.metadata


def _migration_database_url() -> str:
    """Allow isolated migration tests/tools to select a URL without mutating runtime settings."""
    return str(config.attributes.get("database_url") or DATABASE_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = _migration_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        backup_sqlite_db(url)
        context.run_migrations()


def _connect_with_retry(connectable):
    """Open a database connection without retrying partially applied migrations."""
    max_retries = 5
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            return connectable.connect()
        except OperationalError as exc:
            if attempt == max_retries - 1:
                print("Max retries reached. Could not connect to database.")
                raise
            print(f"Database connection failed: {exc}. Retrying in {retry_delay}s... ({attempt + 1}/{max_retries})")
            time.sleep(retry_delay)


def run_migrations_online() -> None:
    """Run migrations once after establishing a retryable connection."""
    # Wir überschreiben die sqlalchemy.url in der config mit der aus DATABASE_URL
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _migration_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with _connect_with_retry(connectable) as connection:
        uses_postgres_lock = connection.dialect.name == "postgresql"
        if uses_postgres_lock:
            connection.exec_driver_sql("SELECT pg_advisory_lock(2044597616)")
            connection.commit()
        try:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
            )

            with context.begin_transaction():
                context.run_migrations()
        finally:
            if uses_postgres_lock:
                connection.exec_driver_sql("SELECT pg_advisory_unlock(2044597616)")
                connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
