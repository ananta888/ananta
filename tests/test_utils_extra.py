import pytest
from agent.utils import _extract_command, _extract_reason, _extract_tool_calls, read_json, write_json
import os
import json

def test_extract_command_json():
    text = '{"command": "ls -la", "reason": "list files"}'
    assert _extract_command(text) == "ls -la"
    
    text_with_markdown = 'Here is the command:\n```json\n{"command": "whoami"}\n```'
    assert _extract_command(text_with_markdown) == "whoami"

def test_extract_command_fix_json():
    text = '{"command": "echo hello"'
    assert _extract_command(text) == "echo hello"

def test_extract_command_markdown():
    text = "```bash\napt-get update\n```"
    assert _extract_command(text) == "apt-get update"
    
    text = "```powershell\nGet-Process\n```"
    assert _extract_command(text) == "Get-Process"

def test_extract_command_generic_block():
    text = "```\njust-a-command\n```"
    assert _extract_command(text) == "just-a-command"
    
    text = "```python\nprint('hello')\n```"
    assert _extract_command(text) == "print('hello')"

def test_extract_command_plain_text():
    text = "simple-command"
    assert _extract_command(text) == "simple-command"

def test_extract_reason_json():
    text = '{"command": "ls", "reason": "testing reason"}'
    assert _extract_reason(text) == "testing reason"
    
    text = '{"thought": "explaining stuff"}'
    assert _extract_reason(text) == "explaining stuff"

def test_extract_reason_markdown():
    text = "This is why I do it: ```bash\nls\n```"
    assert _extract_reason(text) == "This is why I do it:"

def test_extract_reason_fallback():
    text = "Just some text"
    assert _extract_reason(text) == "Just some text"
    
    assert _extract_reason("") == "Keine Begründung angegeben."

def test_extract_tool_calls():
    text = '{"tool_calls": [{"name": "read_file", "arguments": {"path": "test.txt"}}]}'
    calls = _extract_tool_calls(text)
    assert calls is not None
    assert len(calls) == 1
    assert calls[0]["name"] == "read_file"

def test_json_io(tmp_path):
    test_file = tmp_path / "test.json"
    data = {"key": "value"}
    
    write_json(str(test_file), data)
    assert test_file.exists()
    
    loaded = read_json(str(test_file))
    assert loaded == data

def test_read_json_not_found():
    assert read_json("non_existent_file.json", default=[]) == []

def test_write_json_error():
    from agent.common.errors import PermanentError
    # Use a character that is invalid in Windows filenames to trigger an error
    # We want to trigger it inside the try-except block of write_json
    # But os.makedirs outside might catch it first if we are not careful.
    # We use a path where the directory exists but the filename is invalid.
    invalid_path = '?:/"' 
    with pytest.raises((PermanentError, FileNotFoundError)):
        write_json(invalid_path, {"test": 1})

def test_update_json(tmp_path):
    from agent.utils import update_json
    test_file = tmp_path / "update.json"
    write_json(str(test_file), {"count": 1})
    
    def inc(data):
        data["count"] += 1
        return data
        
    update_json(str(test_file), inc)
    assert read_json(str(test_file))["count"] == 2

def test_http_methods_failure():
    from agent.utils import _http_get, _http_post
    # Use a non-existent local port to trigger ConnectionError
    assert _http_get("http://localhost:1") is None
    assert _http_post("http://localhost:1", {"data": 1}) is None

def test_validate_request_success(app):
    from agent.utils import validate_request
    from pydantic import BaseModel
    
    class Model(BaseModel):
        name: str
    
    @app.route("/test_val", methods=["POST"])
    @validate_request(Model)
    def test_val():
        return "success"
    
    with app.test_client() as client:
        response = client.post("/test_val", json={"name": "test"})
        assert response.status_code == 200
        assert response.data.decode() == "success"
        
        response = client.post("/test_val", json={"wrong": "field"})
        assert response.status_code == 422

def test_archive_old_tasks_logic(tmp_path, monkeypatch):
    from agent.utils import _archive_old_tasks
    import agent.utils
    import time
    
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    tasks_file = data_dir / "tasks.json"
    archive_dir = data_dir / "archive"
    archive_dir.mkdir()
    
    # Mock get_data_dir to return our temp data dir
    monkeypatch.setattr(agent.utils, "get_data_dir", lambda: str(data_dir))
    
    # Old task (based on created_at)
    old_task = {
        "id": "old",
        "status": "done",
        "created_at": time.time() - (86400 * 31) # 31 days ago
    }
    # Recent task
    new_task = {
        "id": "new",
        "status": "todo"
    }
    
    write_json(str(tasks_file), {"old": old_task, "new": new_task})
    
    _archive_old_tasks(tasks_path=str(tasks_file))
    
    # Verify tasks.json only contains "new"
    tasks = read_json(str(tasks_file))
    assert "new" in tasks
    assert "old" not in tasks
    
    # Verify archive contains "old"
    archive_file = data_dir / "tasks_archive.json"
    assert archive_file.exists()
    archive = read_json(str(archive_file))
    assert "old" in archive
