from __future__ import annotations

import sqlite3

from agent.services.dendritic_memory_migration import (
    downgrade_job_store,
    downgrade_registry_store,
    upgrade_job_store,
    upgrade_registry_store,
)


def test_additive_upgrade_and_downgrade_preserve_legacy_lora_tables(tmp_path) -> None:
    path = tmp_path / "compatibility.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_lora_jobs(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_lora_jobs VALUES('lora-1','unchanged')")
        upgrade_job_store(connection)
        upgrade_registry_store(connection)
        upgrade_job_store(connection)
        upgrade_registry_store(connection)
        downgrade_registry_store(connection)
        downgrade_job_store(connection)
        assert connection.execute("SELECT payload FROM legacy_lora_jobs WHERE id='lora-1'").fetchone() == (
            "unchanged",
        )
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "legacy_lora_jobs" in names
    assert not any(name.startswith("dendritic_pack_") or name == "dendritic_job_revisions" for name in names)
