from sqlalchemy import create_engine, text, inspect
from pathlib import Path


def test_ensure_schema_compat_adds_depends_on_columns(monkeypatch):
    import agent.database as db
    import tempfile
    import os

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

    assert "depends_on" in task_cols
    assert "depends_on" in arch_cols
    assert "mfa_backup_codes" in user_cols

    with temp_engine.connect() as conn:
        t_dep = conn.execute(text("SELECT depends_on FROM tasks WHERE id='t1'")).scalar()
        a_dep = conn.execute(text("SELECT depends_on FROM archived_tasks WHERE id='a1'")).scalar()
    assert t_dep == "[]"
    assert a_dep == "[]"
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
