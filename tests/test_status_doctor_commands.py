from __future__ import annotations

import json

from agent.cli import doctor
from agent.cli import main as cli_main


def test_status_command_routes_to_cli_goals_status(monkeypatch) -> None:
    captured: dict[str, list[str] | None] = {}

    def fake_run_cli_goals(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(cli_main, "run_cli_goals", fake_run_cli_goals)

    rc = cli_main.main(["status", "--help"])

    assert rc == 0
    assert captured["argv"] == ["--status", "--help"]


def test_first_run_command_routes_to_cli_goals_first_run(monkeypatch) -> None:
    captured: dict[str, list[str] | None] = {}

    def fake_run_cli_goals(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(cli_main, "run_cli_goals", fake_run_cli_goals)

    rc = cli_main.main(["first-run"])

    assert rc == 0
    assert captured["argv"] == ["--first-run"]


def test_doctor_reports_actionable_steps_when_setup_is_missing(tmp_path) -> None:
    lines: list[str] = []
    rc = doctor.main([], cwd=tmp_path, env={"ANANTA_BASE_URL": "http://localhost:5000"}, output_fn=lines.append)

    assert rc == 1
    text = "\n".join(lines)
    assert "Missing config.json" in text
    assert "Run `ananta init" in text


def test_doctor_json_mode_emits_structured_checks(tmp_path) -> None:
    (tmp_path / "config.json").write_text("{}\n", encoding="utf-8")
    lines: list[str] = []

    rc = doctor.main(["--json"], cwd=tmp_path, env={"ANANTA_BASE_URL": "http://localhost:5000"}, output_fn=lines.append)

    assert rc == 0
    payload = json.loads(lines[-1])
    assert payload["summary"]["failures"] == 0
    assert len(payload["checks"]) >= 1
