from __future__ import annotations

import json

from scripts.ai_snake_training_data import main


def test_cli_export_stdout_returns_valid_json(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    code = main(["export", "--stdout"])
    assert code == 0
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["schema_version"] == "ai_snake_training_bundle.v1"


def test_cli_validate_and_summarize(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    bundle_path = tmp_path / "bundle.json"
    export_code = main(["export", "--stdout"])
    assert export_code == 0
    exported = capsys.readouterr().out.strip()
    bundle_path.write_text(exported, encoding="utf-8")

    code_validate = main(["validate", str(bundle_path)])
    assert code_validate == 0
    assert "valid" in capsys.readouterr().out

    code_summary = main(["summarize", str(bundle_path)])
    assert code_summary == 0
    assert "bundle schema=ai_snake_training_bundle.v1" in capsys.readouterr().out


def test_cli_validate_invalid_schema_returns_nonzero(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "ai_snake_training_bundle.v1", "bundle_id": "x"}), encoding="utf-8")
    code = main(["validate", str(bad)])
    assert code != 0
    assert "invalid" in capsys.readouterr().out
