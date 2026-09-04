from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_default_sandbox_has_no_tools_network_secrets_or_writes() -> None:
    sandbox = json.loads((ROOT / "config/security/unsafe-research-sandbox.v1.json").read_text())
    assert sandbox["network"] == "none"
    assert sandbox["tools"] == {"default": "deny", "allowed": []}
    assert sandbox["filesystem"]["workspace"] == "read_only"
    assert sandbox["filesystem"]["host_paths"] is False
    assert sandbox["process"] == {
        "shell": False,
        "git_write": False,
        "browser": False,
        "messages": False,
        "database_write": False,
    }
    assert sandbox["container"]["docker_socket"] is False
