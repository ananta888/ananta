import pytest
import time
from unittest.mock import MagicMock, patch
from agent.utils import (
    get_data_dir, get_host_gateway_ip, validate_request, 
    _archive_terminal_logs, _cleanup_old_backups, _archive_old_tasks,
    _http_get, _http_post, rate_limit, _extract_command, _extract_reason,
    _extract_tool_calls, read_json, write_json, update_json,
    register_with_hub, _get_approved_command, log_to_db,
    _log_terminal_entry, log_llm_entry
)
from agent.common.errors import PermanentError, TransientError
from pydantic import BaseModel
from flask import Flask, g
import os
import json
import portalocker

def test_get_host_gateway_ip_success():
    with patch("subprocess.check_output") as mock_run:
        # Mocking the awk part as well since we call it via shell=True
        # Actually subprocess.check_output returns the output of the whole shell command
        mock_run.return_value = b"172.17.0.1\n"
        assert get_host_gateway_ip() == "172.17.0.1"

def test_get_host_gateway_ip_failure():
    with patch("subprocess.check_output") as mock_run:
        mock_run.side_effect = Exception("error")
        assert get_host_gateway_ip() is None

def test_get_host_gateway_ip_no_match():
    with patch("subprocess.check_output") as mock_run:
        mock_run.return_value = b"no default route"
        assert get_host_gateway_ip() is None

def test_rate_limit(app):
    @app.route("/rate-limit")
    @rate_limit(limit=2, window=10)
    def limited_route():
        return "ok"
    
    with app.test_client() as client:
        # First call
        assert client.get("/rate-limit").status_code == 200
        # Second call
        assert client.get("/rate-limit").status_code == 200
        # Third call (should be limited)
        assert client.get("/rate-limit").status_code == 429

def test_get_data_dir_runtime_error():
    # Test get_data_dir when called outside of application context
    # This should trigger the RuntimeError in current_app access
    # Mocking settings.data_dir
    with patch("agent.config.settings.data_dir", "default_dir"):
        assert get_data_dir() == "default_dir"

def test_archive_terminal_logs_full(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.utils.get_data_dir", lambda: str(tmp_path))
    log_file = tmp_path / "terminal_log.jsonl"
    
    # Create entries: one old, one new
    now = time.time()
    old_entry = {"timestamp": now - 86400 * 40, "cmd": "old"}
    new_entry = {"timestamp": now, "cmd": "new"}
    
    with open(log_file, "w") as f:
        f.write(json.dumps(old_entry) + "\n")
        f.write(json.dumps(new_entry) + "\n")
        f.write("invalid json\n")
    
    # Reset the global last check time to force run
    import agent.utils
    agent.utils._last_terminal_archive_check = 0
    
    with patch("agent.utils.settings.tasks_retention_days", 30):
        _archive_terminal_logs()
    
    # Verify log_file has new entry and invalid json (as it's kept on error)
    with open(log_file, "r") as f:
        lines = f.readlines()
        assert len(lines) == 2
        assert "new" in lines[0]
        assert "invalid" in lines[1]
    
    # Verify archive has old entry
    archive_file = tmp_path / "terminal_log_archive.jsonl"
    assert archive_file.exists()
    with open(archive_file, "r") as f:
        lines = f.readlines()
        assert len(lines) == 1
        assert "old" in lines[0]

def test_cleanup_old_backups(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.utils.get_data_dir", lambda: str(tmp_path))
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    
    # Create some old and new backup files
    old_file = backup_dir / "old_backup.zip"
    old_file.touch()
    
    # cutoff is now - 7 days (default settings.backup_retention_days)
    old_time = time.time() - (86400 * 10)
    os.utime(str(old_file), (old_time, old_time))
    
    new_file = backup_dir / "new_backup.zip"
    new_file.touch()
    
    # Mock settings.backups_retention_days correctly (note the 's' in backups_retention_days)
    with patch("agent.utils.settings.backups_retention_days", 7):
        _cleanup_old_backups()
    
    assert not old_file.exists()
    assert new_file.exists()

def test_register_with_hub_failure():
    with patch("agent.utils._http_post") as mock_post:
        # Side effect to raise an exception, as register_with_hub returns False on exception
        mock_post.side_effect = Exception("failed")
        assert register_with_hub("http://hub", "agent", 8000, "token") is False

def test_get_approved_command_success():
    with patch("agent.utils._http_post") as mock_post:
        # register_with_hub returns cmd if approved
        mock_post.return_value = {"status": "approved"}
        assert _get_approved_command("http://hub", "ls", "reason") == "ls"

def test_get_approved_command_denied():
    with patch("agent.utils._http_post") as mock_post:
        # If not explicit override or SKIP, it returns the original cmd?
        # Let's check _get_approved_command logic again.
        # It returns None for SKIP.
        mock_post.return_value = "SKIP"
        assert _get_approved_command("http://hub", "rm -rf /", "reason") is None

def test_log_to_db():
    # log_to_db currently does nothing (based on structure)
    log_to_db("name", "info", "msg")

def test_log_terminal_entry(app):
    with app.app_context():
        # This function uses current_app.config.get("AGENT_NAME")
        app.config["AGENT_NAME"] = "test-agent"
        # and calls _archive_terminal_logs which we want to avoid for now or mock
        with patch("agent.utils._archive_terminal_logs"):
            with patch("portalocker.Lock") as mock_lock:
                _log_terminal_entry("test-agent", 1, "in", command="ls")
                assert mock_lock.called

def test_log_llm_entry(app):
    with app.app_context():
        app.config["AGENT_NAME"] = "test-agent"
        with patch("portalocker.Lock") as mock_lock:
            log_llm_entry("request", prompt="hi")
            assert mock_lock.called

def test_update_json_error(tmp_path):
    from agent.utils import update_json
    test_file = tmp_path / "locked.json"
    write_json(str(test_file), {"a": 1})
    
    def fail(data):
        raise Exception("update failed")
        
    with pytest.raises(PermanentError):
        update_json(str(test_file), fail)

def test_http_post_idempotency():
    from agent.utils import _http_post
    with patch("agent.utils.get_default_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Test with headers provided
        _http_post("http://test", {"a": 1}, headers={"X-Test": "val"}, idempotency_key="key123")
        args, kwargs = mock_client.post.call_args
        assert kwargs["idempotency_key"] == "key123"
        assert kwargs["headers"]["X-Test"] == "val"
        
        # Test without headers provided
        _http_post("http://test", {"a": 1}, idempotency_key="key456")
        args, kwargs = mock_client.post.call_args
        assert kwargs["idempotency_key"] == "key456"

def test_archive_old_tasks_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.utils.get_data_dir", lambda: str(tmp_path))
    # Should not raise if tasks.json doesn't exist
    _archive_old_tasks(tasks_path=str(tmp_path / "not_there.json"))

def test_archive_old_tasks_db_exception(monkeypatch):
    # Test that if DB archive fails, it falls back to JSON
    with patch("agent.repository.archived_task_repo.delete_old", side_effect=Exception("DB Error")):
        with patch("agent.utils.settings") as mock_settings:
            mock_settings.data_dir = "some_dir"
            mock_settings.tasks_retention_days = 30
            mock_settings.archived_tasks_retention_days = 90
            # Should not raise, just log warning and continue to JSON logic
            # which will return if file doesn't exist
            _archive_old_tasks()

def test_archive_old_tasks_json_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.utils.get_data_dir", lambda: str(tmp_path))
    tasks_file = tmp_path / "tasks.json"
    archive_file = tmp_path / "tasks_archive.json"
    
    now = time.time()
    # Very old archived task
    old_archived = {"id": "old", "archived_at": now - (100 * 86400)}
    # Recent archived task
    new_archived = {"id": "new", "archived_at": now - (10 * 86400)}
    
    write_json(str(archive_file), {"old": old_archived, "new": new_archived})
    write_json(str(tasks_file), {}) # Active tasks empty
    
    with patch("agent.utils.settings.archived_tasks_retention_days", 90):
        with patch("agent.utils.settings.tasks_retention_days", 30):
            _archive_old_tasks(tasks_path=str(tasks_file))
    
    archived = read_json(str(archive_file))
    assert "old" not in archived
    assert "new" in archived
