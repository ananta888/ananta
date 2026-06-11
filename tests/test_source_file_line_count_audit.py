from __future__ import annotations

import json

from scripts.audit.source_file_line_count import audit, main


def _write(path, lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n" * lines, encoding="utf-8")


def test_source_file_line_count_classifies_source_test_generated_and_excluded(tmp_path):
    _write(tmp_path / "agent" / "service.py", 4)
    _write(tmp_path / "tests" / "test_service.py", 5)
    _write(tmp_path / "frontend" / "bundle.min.js", 6)
    _write(tmp_path / "node_modules" / "dep.js", 7)

    result = audit(root=tmp_path, threshold=3)
    by_path = {row["path"]: row for row in result["files"]}

    assert by_path["agent/service.py"]["category"] == "source"
    assert by_path["tests/test_service.py"]["category"] == "test"
    assert by_path["frontend/bundle.min.js"]["category"] == "generated"
    assert by_path["node_modules/dep.js"]["category"] == "excluded"
    assert result["summary"]["source_over_threshold"] == 1
    assert result["summary"]["test_over_threshold"] == 1
    assert result["summary"]["generated_over_threshold"] == 1


def test_fail_on_source_over_threshold_respects_allowlist(tmp_path, capsys):
    _write(tmp_path / "agent" / "big.py", 5)
    allowlist = tmp_path / "allow.json"
    allowlist.write_text(
        json.dumps({"allowlist": [{"path": "agent/big.py", "reason": "legacy", "expires": "2026-12-31"}]}),
        encoding="utf-8",
    )

    assert main(["--root", str(tmp_path), "--threshold", "3", "--fail-on-source-over-threshold"]) == 1
    capsys.readouterr()
    assert main(
        [
            "--root",
            str(tmp_path),
            "--threshold",
            "3",
            "--allowlist",
            str(allowlist),
            "--fail-on-source-over-threshold",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"][0]["allowlisted"] is True


def test_over_threshold_only_outputs_only_large_files(tmp_path, capsys):
    _write(tmp_path / "agent" / "small.py", 1)
    _write(tmp_path / "agent" / "big.py", 5)

    assert main(["--root", str(tmp_path), "--threshold", "3", "--over-threshold-only"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["path"] for row in payload["files"]] == ["agent/big.py"]
