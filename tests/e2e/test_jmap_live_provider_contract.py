from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.jmap_live_provider_support import (
    LiveJmapProviderConfig,
    write_live_evidence,
)


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "RUN_JMAP_LIVE_PROVIDER_E2E": "1",
        "ANANTA_JMAP_LIVE_DEDICATED_ACCOUNT_ACK": "1",
        "ANANTA_JMAP_LIVE_SESSION_URL": (
            "https://mail.example.test/.well-known/jmap"
        ),
        "ANANTA_JMAP_LIVE_USERNAME": "alice@example.test",
        "ANANTA_JMAP_LIVE_CREDENTIAL": "fixture-live-secret",
        "ANANTA_JMAP_LIVE_RUN_ID": "RUN_provided-123",
        "ANANTA_JMAP_LIVE_EVIDENCE_PATH": str(tmp_path / "evidence.json"),
    }


def test_live_config_requires_explicit_complete_operator_input(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="jmap_live_provider_opt_in_required"):
        LiveJmapProviderConfig.from_environment({}, repo_root=tmp_path)

    incomplete = _environment(tmp_path)
    incomplete.pop("ANANTA_JMAP_LIVE_CREDENTIAL")
    with pytest.raises(ValueError, match="jmap_live_provider_config_incomplete"):
        LiveJmapProviderConfig.from_environment(incomplete, repo_root=tmp_path)

    invalid_run = _environment(tmp_path)
    invalid_run["ANANTA_JMAP_LIVE_RUN_ID"] = "invented"
    with pytest.raises(ValueError, match="jmap_live_run_id_invalid"):
        LiveJmapProviderConfig.from_environment(invalid_run, repo_root=tmp_path)


def test_live_evidence_is_atomic_bounded_and_secret_free(tmp_path: Path) -> None:
    config = LiveJmapProviderConfig.from_environment(
        _environment(tmp_path),
        repo_root=tmp_path,
    )

    write_live_evidence(
        config,
        results={
            "discovery": {
                "status": "completed",
                "reason_code": "ok",
                "counters": {"account_count": 1},
                "credential": "fixture-live-secret",
                "body": "must not be copied",
            }
        },
    )

    raw = config.evidence_path.read_text(encoding="utf-8")
    report = json.loads(raw)
    assert report == {
        "checks": {
            "discovery": {
                "counters": {"account_count": 1},
                "reason_code": "ok",
                "status": "completed",
            }
        },
        "contains_credentials_or_body": False,
        "mutation_restored": True,
        "provider": "jmap",
        "run_id": "RUN_provided-123",
        "schema": "jmap_provider_smoke.v1",
    }
    assert "fixture-live-secret" not in raw
    assert "must not be copied" not in raw
    assert "fixture-live-secret" not in repr(config)
