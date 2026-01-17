import os
import sys
import time
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy.exc import OperationalError

from alembic import context

# Projekt-Root zum Pfad hinzufügen
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), '..')))

# Importiere SQLModel und Modelle
from sqlmodel import SQLModel
from agent.db_models import *  # Damit die Metadaten gefüllt sind
from agent.database import DATABASE_URL

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata = mymodel.Base.metadata
target_metadata = SQLModel.metadata

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Wir überschreiben die sqlalchemy.url in der config mit der aus DATABASE_URL
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = DATABASE_URL
    
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # Retry-Logik für Datenbankverbindung (hilfreich in Docker-Umgebungen)
    max_retries = 5
    retry_delay = 5
    last_exception = None

    for i in range(max_retries):
        try:
            with connectable.connect() as connection:
                context.configure(
                    connection=connection, 
                    target_metadata=target_metadata,
                    render_as_batch=True
                )

                with context.begin_transaction():
                    context.run_migrations()
            return
        except OperationalError as e:
            last_exception = e
            if i < max_retries - 1:
                print(f"Database connection failed: {e}. Retrying in {retry_delay}s... ({i+1}/{max_retries})")
                time.sleep(retry_delay)
            else:
                print(f"Max retries reached. Could not connect to database.")
                raise last_exception


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
