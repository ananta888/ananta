from __future__ import annotations

from scripts.run_semantic_media_game_day import (
    SCENARIO_COMMANDS,
    SCENARIOS,
    evaluate_live,
    execute_local,
    unavailable,
    validate_runbooks,
)


def test_runbooks_cover_operations_incidents_privacy_and_rollback() -> None:
    source_digest, measurements = validate_runbooks()
    assert len(source_digest) == 64 and measurements["runbook_count"] == 4
    assert SCENARIOS == {"hub-failover", "sfu-failover", "revocation", "worker-drain", "full-feature-rollback"}
    assert set(SCENARIO_COMMANDS) == SCENARIOS
    assert all(paths and all(path.startswith("tests/") for path in paths) for paths in SCENARIO_COMMANDS.values())


def test_missing_live_game_day_is_honestly_release_blocking() -> None:
    evidence = unavailable()
    assert evidence.status == "unverified"
    assert evidence.release_blocking
    assert evidence.reason_codes == ("live_game_day_evidence_unavailable",)


def test_local_game_day_runs_each_failure_domain_in_an_isolated_process(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    class Completed:
        returncode = 0

    def run(command, **kwargs):
        calls.append(tuple(command))
        assert kwargs["env"]["RUN_INTEGRATION_TESTS"] == "1"
        assert kwargs["capture_output"] is True
        return Completed()

    monkeypatch.setattr("scripts.run_semantic_media_game_day.subprocess.run", run)
    report = execute_local()
    assert len(calls) == len(SCENARIOS)
    assert {row["name"] for row in report["scenarios"]} == SCENARIOS
    assert evaluate_live(report).status == "passed"
