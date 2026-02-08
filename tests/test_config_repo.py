import json
import pytest
from agent.db_models import ConfigDB
from agent.repository import ConfigRepository
from agent.routes.config import unwrap_config

def test_config_repo_save_and_get(db_session):
    repo = ConfigRepository()
    
    # Test einfache Speicherung
    cfg = ConfigDB(key="test_key", value_json=json.dumps("test_value"))
    repo.save(cfg)
    
    retrieved = repo.get_by_key("test_key")
    assert retrieved is not None
    assert json.loads(retrieved.value_json) == "test_value"

def test_config_repo_overwrite(db_session):
    repo = ConfigRepository()
    
    # Speichern
    repo.save(ConfigDB(key="theme", value_json=json.dumps("dark")))
    
    # Überschreiben
    repo.save(ConfigDB(key="theme", value_json=json.dumps("light")))
    
    retrieved = repo.get_by_key("theme")
    assert json.loads(retrieved.value_json) == "light"

def test_config_repo_get_all(db_session):
    repo = ConfigRepository()
    
    repo.save(ConfigDB(key="k1", value_json=json.dumps("v1")))
    repo.save(ConfigDB(key="k2", value_json=json.dumps("v2")))
    
    all_cfgs = repo.get_all()
    assert len(all_cfgs) >= 2
    keys = [c.key for c in all_cfgs]
    assert "k1" in keys
    assert "k2" in keys

def test_unwrap_config_simple():
    data = {"foo": "bar"}
    unwrapped = unwrap_config(data)
    assert unwrapped == {"foo": "bar"}

def test_unwrap_config_wrapped():
    data = {
        "status": "success",
        "data": {
            "llm_config": {
                "status": "success",
                "data": {
                    "provider": "ollama"
                }
            }
        }
    }
    unwrapped = unwrap_config(data)
    assert unwrapped == {"llm_config": {"provider": "ollama"}}

def test_unwrap_config_deep_nesting():
    data = {
        "status": "success",
        "data": {
            "a": {
                "status": "success",
                "data": {
                    "b": {
                        "status": "success",
                        "data": "final_value"
                    }
                }
            }
        }
    }
    unwrapped = unwrap_config(data)
    assert unwrapped == {"a": {"b": "final_value"}}

def test_unwrap_config_no_dict():
    assert unwrap_config("string") == "string"
    assert unwrap_config(123) == 123
    assert unwrap_config(None) is None
