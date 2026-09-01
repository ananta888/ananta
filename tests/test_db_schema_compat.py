import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import QueuePool, StaticPool


def test_named_memory_database_supports_distinct_concurrent_connections():
    import agent.database as db

    assert db._is_in_memory_sqlite(db.DATABASE_URL)
    assert not isinstance(db.engine.pool, StaticPool)
    assert isinstance(db.engine.pool, QueuePool)

    rendezvous = threading.Barrier(2)

    def connection_identity() -> int:
        with db.engine.connect() as connection:
            rendezvous.wait(timeout=5)
            connection.execute(text("SELECT 1")).scalar_one()
            return id(connection.connection.dbapi_connection)

    with ThreadPoolExecutor(max_workers=2) as executor:
        identities = list(executor.map(lambda _: connection_identity(), range(2)))

    assert len(set(identities)) == 2


def test_ensure_schema_compat_does_not_mutate_schema_at_runtime(monkeypatch):
    import os
    import tempfile

    import agent.database as db

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    temp_engine = create_engine(f"sqlite:///{db_path}")
    with temp_engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT, role TEXT)"))
        conn.execute(text("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT)"))
        conn.execute(text("CREATE TABLE archived_tasks (id TEXT PRIMARY KEY, status TEXT)"))
        conn.execute(text("INSERT INTO tasks (id, status) VALUES ('t1', 'todo')"))
        conn.execute(text("INSERT INTO archived_tasks (id, status) VALUES ('a1', 'archived')"))

    monkeypatch.setattr(db, "engine", temp_engine)
    db._ensure_schema_compat()

    insp = inspect(temp_engine)
    task_cols = {c["name"] for c in insp.get_columns("tasks")}
    arch_cols = {c["name"] for c in insp.get_columns("archived_tasks")}
    user_cols = {c["name"] for c in insp.get_columns("users")}

    assert "depends_on" not in task_cols
    assert "depends_on" not in arch_cols
    assert "mfa_backup_codes" not in user_cols
    temp_engine.dispose()
    try:
        os.remove(db_path)
    except PermissionError:
        pass


def test_alembic_contains_depends_on_migration():
    mig = Path("migrations/versions/7b3c4d5e6f7a_add_depends_on_columns.py")
    assert mig.exists()
    content = mig.read_text(encoding="utf-8")
    assert "down_revision" in content and "6f9a1b2c3d4e" in content
    assert "depends_on" in content


def test_alembic_contains_canonical_status_backfill_migration():
    mig = Path("migrations/versions/8c1d2e3f4a5b_backfill_canonical_task_statuses.py")
    assert mig.exists()
    content = mig.read_text(encoding="utf-8")
    assert "down_revision" in content and "7b3c4d5e6f7a" in content
    assert "backfill" in content.lower()


def test_ensure_schema_compat_does_not_backfill_legacy_task_status_aliases(monkeypatch):
    import os
    import tempfile

    import agent.database as db

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    temp_engine = create_engine(f"sqlite:///{db_path}")
    with temp_engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT, role TEXT)"))
        conn.execute(text("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT)"))
        conn.execute(text("CREATE TABLE archived_tasks (id TEXT PRIMARY KEY, status TEXT)"))
        conn.execute(text("INSERT INTO tasks (id, status) VALUES ('s1', 'done')"))
        conn.execute(text("INSERT INTO tasks (id, status) VALUES ('s2', 'in-progress')"))
        conn.execute(text("INSERT INTO tasks (id, status) VALUES ('s3', 'to-do')"))
        conn.execute(text("INSERT INTO archived_tasks (id, status) VALUES ('a1', 'backlog')"))

    monkeypatch.setattr(db, "engine", temp_engine)
    db._ensure_schema_compat()

    with temp_engine.connect() as conn:
        rows = conn.execute(text("SELECT id, status FROM tasks ORDER BY id")).fetchall()
        archived = conn.execute(text("SELECT id, status FROM archived_tasks ORDER BY id")).fetchall()

    assert dict(rows)["s1"] == "done"
    assert dict(rows)["s2"] == "in-progress"
    assert dict(rows)["s3"] == "to-do"
    assert dict(archived)["a1"] == "backlog"


def test_maintenance_script_for_status_backfill_exists():
    script = Path("devtools/backfill_task_statuses.py")
    assert script.exists()


def test_ensure_schema_compat_backfills_legacy_agents_registration_columns(monkeypatch):
    import os
    import tempfile

    import agent.database as db

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    temp_engine = create_engine(f"sqlite:///{db_path}")
    with temp_engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE agents (
                    url TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    token TEXT,
                    worker_roles TEXT NOT NULL DEFAULT '[]',
                    capabilities TEXT NOT NULL DEFAULT '[]',
                    execution_limits TEXT NOT NULL DEFAULT '{}',
                    last_seen REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'online'
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO agents (url, name, role, token, last_seen, status) "
                "VALUES ('http://worker-a:5000', 'worker-a', 'worker', 'tok', 1.0, 'online')"
            )
        )

    monkeypatch.setattr(db, "engine", temp_engine)
    db._ensure_schema_compat()

    insp = inspect(temp_engine)
    columns = {c["name"] for c in insp.get_columns("agents")}
    assert "registration_validated" in columns
    assert "registration_provenance" in columns
    assert "authorized_capabilities" in columns
    assert "validation_errors" in columns
    assert "validated_at" in columns

    with temp_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT registration_validated, registration_provenance, "
                "authorized_capabilities, validation_errors, validated_at "
                "FROM agents WHERE url = 'http://worker-a:5000'"
            )
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 1
    assert row[1] == "legacy"
    assert str(row[2] or "") == "[]"
    assert str(row[3] or "") == "[]"
    assert row[4] is None

    temp_engine.dispose()
    try:
        os.remove(db_path)
    except PermissionError:
        pass


def test_alembic_contains_strict_worker_registration_provenance_migration():
    migration = Path(
        "migrations/versions/"
        "v1w2x3y4z5a6_add_agent_registration_provenance.py"
    )
    assert migration.exists()
    content = migration.read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "u1v2w3x4y5z6"' in content
    assert "registration_provenance" in content
    assert "authorized_capabilities" in content


def test_alembic_contains_hub_worker_assignment_migration():
    migration = Path(
        "migrations/versions/"
        "w1x2y3z4a5b6_add_workflow_worker_assignments.py"
    )
    assert migration.exists()
    content = migration.read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "v1w2x3y4z5a6"' in content
    assert "workflow_worker_assignments" in content
    assert "uq_workflow_worker_assignment_step" in content
