from __future__ import annotations

import json

from agent.cli.commands.runtime import dispatch


def test_runtime_llm_interceptor_dry_run(tmp_path, capsys):
    cfg = {
        "upstreams": [
            {
                "id": "local",
                "type": "openai_compatible",
                "base_url": "http://127.0.0.1:1234/v1",
                "trust_level": "local",
                "allowed_models": ["intercepted-coder"],
            }
        ],
        "routing": {"default_upstream": "local", "default_model": "intercepted-coder", "rules": []},
    }
    path = tmp_path / "llmi.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    code = dispatch(["llm-interceptor", "--config", str(path), "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "LLM interceptor bind" in out
    assert "upstreams=['local']" in out

