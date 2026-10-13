import copy
import json
from pathlib import Path

import pytest

from agent.services.memory_channel_registry import MemoryChannelRegistry
from scripts.check_memory_channels import channel_signals, validate


def inventory(files):
    return {"schema_version": "ananta.memory-channels.v1", "channels": [{
        "id": "test-channel", "owner": "Hub test fixture", "scope": "test task",
        "persistence": "test file", "readers": "test reader", "writers": "test writer",
        "deletion": "temporary directory cleanup", "trust": "untrusted test data", "files": files,
    }]}


def source(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_new_channel_is_rejected_and_exact_registration_admits_it(tmp_path):
    name = "agent/services/new_memory.py"
    source(tmp_path, name, "values = {}\n")
    assert validate(tmp_path, inventory({"agent/old_memory.py": ["named_state_component"]})) == [
        f"{name}:memory_channel_unregistered", "agent/old_memory.py:memory_channel_file_missing",
    ]
    assert validate(tmp_path, inventory({name: ["named_state_component"]})) == []


def test_existing_channel_cannot_gain_new_storage_family_silently(tmp_path):
    name = "worker/core/new_memory.py"
    source(tmp_path, name, "from pathlib import Path\nPath('hidden').write_text('state')\n")
    value = inventory({name: ["named_state_component"]})
    assert validate(tmp_path, value) == [f"{name}:memory_channel_signal_unregistered:filesystem_write"]


@pytest.mark.parametrize("content, signal", [
    ("import sqlite3 as hidden\nhidden.connect('state.db')", "storage_import:sqlite3"),
    ("from redis import Redis", "storage_import:redis"),
    ("from functools import lru_cache\n@lru_cache()\ndef f(): return 1", "process_cache"),
    ("open('state', mode='a')", "filesystem_write"),
    ("open('state', mode=selected_mode)", "dynamic_file_mode"),
    ("class Innocent(SQLModel, table=True):\n __tablename__ = 'hidden_state'", "sql_table:hidden_state"),
])
def test_non_memory_filename_does_not_hide_observable_state(content, signal):
    assert signal in channel_signals("worker/tools/innocent.py", content)


def test_new_table_in_already_registered_file_requires_explicit_inventory_update(tmp_path):
    name = "agent/db_models/state.py"
    source(tmp_path, name, "class A(SQLModel, table=True):\n __tablename__ = 'first'\n"
                          "class B(SQLModel, table=True):\n __tablename__ = 'second'\n")
    assert validate(tmp_path, inventory({name: ["sql_table:first"]})) == [
        f"{name}:memory_channel_signal_unregistered:sql_table:second",
    ]


def test_discovery_does_not_execute_code(tmp_path):
    source(tmp_path, "worker/hidden_cache.py", "raise RuntimeError('must not execute')")
    assert validate(tmp_path, inventory({"worker/hidden_cache.py": ["named_state_component"]})) == []


@pytest.mark.parametrize("field", ["owner", "scope", "persistence", "readers", "writers", "deletion", "trust"])
def test_registration_requires_lifecycle_and_ownership_metadata(tmp_path, field):
    value = inventory({"agent/memory.py": []})
    value["channels"][0].pop(field)
    assert "memory_channel_metadata_missing" in validate(tmp_path, value)


def test_duplicate_registration_is_rejected(tmp_path):
    source(tmp_path, "agent/memory.py", "")
    value = inventory({"agent/memory.py": ["named_state_component"]})
    value["channels"].append(copy.deepcopy(value["channels"][0]))
    failures = validate(tmp_path, value)
    assert "memory_channel_id_invalid" in failures and "agent/memory.py:memory_channel_duplicate" in failures


def test_registration_cannot_escape_repository(tmp_path):
    assert "../outside.py:memory_channel_path_invalid" in validate(tmp_path, inventory({"../outside.py": []}))


def test_runtime_catalog_is_read_only_and_does_not_authorize_any_operation(tmp_path):
    path = tmp_path / "register.json"
    path.write_text(json.dumps(inventory({"agent/memory.py": []})))
    catalog = MemoryChannelRegistry.from_path(path)
    assert catalog.contains("test-channel") and not catalog.contains("unknown")
    assert not hasattr(catalog, "register")


def test_runtime_catalog_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "register.json"
    path.write_text('{"schema_version":"ananta.memory-channels.v1","schema_version":"other"}')
    with pytest.raises(ValueError, match="duplicate_key"):
        MemoryChannelRegistry.from_path(path)


def test_checked_in_catalog_is_loadable():
    path = Path(__file__).resolve().parents[2] / "config/security/memory-channels.v1.json"
    assert MemoryChannelRegistry.from_path(path).contains("hub-result-memory")
