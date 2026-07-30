from __future__ import annotations

from pathlib import Path

from scripts.model_intelligence_acceptance_report import build_report


def _junit(path: Path, *, failures: int = 0) -> None:
    path.write_text(
        (
            '<testsuite tests="3" failures="'
            f'{failures}" errors="0" skipped="0"></testsuite>'
        ),
        encoding="utf-8",
    )


def test_passing_tests_without_grounded_ids_remain_unverified(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "junit.xml"
    _junit(junit)

    report = build_report(
        profile="core",
        junit_path=junit,
        source_ids=(),
        run_ids=(),
        tool_digest="a" * 64,
        container_digest=None,
    )

    assert report["status"] == "unverified"
    assert report["release_allowed"] is False
    assert report["codecompass_repository_gate_reused"] is False


def test_failed_tests_cannot_be_overridden_by_evidence_ids(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "junit.xml"
    _junit(junit, failures=1)

    report = build_report(
        profile="extended",
        junit_path=junit,
        source_ids=(),
        run_ids=(),
        tool_digest="a" * 64,
        container_digest=None,
    )

    assert report["status"] == "failed"
    assert report["release_allowed"] is False


def test_junit_digest_ignores_volatile_runner_metadata(tmp_path: Path) -> None:
    first = tmp_path / "first.xml"
    second = tmp_path / "second.xml"
    first.write_text(
        (
            '<testsuite tests="1" failures="0" errors="0" skipped="0" '
            'time="1.25" timestamp="2026-07-30T08:00:00+02:00" '
            'hostname="runner-a"><testcase classname="tests.test_gate" '
            'name="test_passes" time="1.24" /></testsuite>'
        ),
        encoding="utf-8",
    )
    second.write_text(
        (
            '<testsuite tests="1" failures="0" errors="0" skipped="0" '
            'time="9.75" timestamp="2027-08-31T09:00:00+02:00" '
            'hostname="runner-b"><testcase classname="tests.test_gate" '
            'name="test_passes" time="9.74" /></testsuite>'
        ),
        encoding="utf-8",
    )

    first_report = build_report(
        profile="core",
        junit_path=first,
        source_ids=(),
        run_ids=(),
        tool_digest="a" * 64,
        container_digest=None,
    )
    second_report = build_report(
        profile="core",
        junit_path=second,
        source_ids=(),
        run_ids=(),
        tool_digest="a" * 64,
        container_digest=None,
    )

    assert first_report["junit_sha256"] == second_report["junit_sha256"]
