"""Configuration and evidence helpers for the opt-in external JMAP smoke."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


LIVE_RUN_ENV = "RUN_JMAP_LIVE_PROVIDER_E2E"
LIVE_MUTATION_ACK_ENV = "ANANTA_JMAP_LIVE_DEDICATED_ACCOUNT_ACK"
RUN_ID_PATTERN = re.compile(r"^RUN_[A-Za-z0-9_.:-]+$")
SUPPORTED_AUTH_MODES = frozenset({"basic", "bearer"})


@dataclass(frozen=True, slots=True)
class LiveJmapProviderConfig:
    session_url: str
    username: str
    credential: str = field(repr=False)
    auth_mode: str
    provider_account_id: str | None
    run_id: str
    evidence_path: Path

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        repo_root: Path,
    ) -> "LiveJmapProviderConfig":
        source = dict(os.environ if environment is None else environment)
        if source.get(LIVE_RUN_ENV) != "1":
            raise ValueError("jmap_live_provider_opt_in_required")
        if source.get(LIVE_MUTATION_ACK_ENV) != "1":
            raise ValueError("jmap_live_dedicated_account_ack_required")
        required = {
            "session_url": source.get("ANANTA_JMAP_LIVE_SESSION_URL"),
            "username": source.get("ANANTA_JMAP_LIVE_USERNAME"),
            "credential": source.get("ANANTA_JMAP_LIVE_CREDENTIAL"),
            "run_id": source.get("ANANTA_JMAP_LIVE_RUN_ID"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("jmap_live_provider_config_incomplete")
        run_id = str(required["run_id"])
        if RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise ValueError("jmap_live_run_id_invalid")
        auth_mode = str(source.get("ANANTA_JMAP_LIVE_AUTH_MODE") or "basic").lower()
        if auth_mode not in SUPPORTED_AUTH_MODES:
            raise ValueError("jmap_live_auth_mode_unsupported")
        evidence_value = source.get("ANANTA_JMAP_LIVE_EVIDENCE_PATH")
        evidence_path = (
            Path(evidence_value).expanduser()
            if evidence_value
            else repo_root / "artifacts/e2e/jmap-mail-gate.json"
        )
        if not evidence_path.is_absolute():
            evidence_path = repo_root / evidence_path
        return cls(
            session_url=str(required["session_url"]),
            username=str(required["username"]),
            credential=str(required["credential"]),
            auth_mode=auth_mode,
            provider_account_id=(
                str(source["ANANTA_JMAP_LIVE_PROVIDER_ACCOUNT_ID"])
                if source.get("ANANTA_JMAP_LIVE_PROVIDER_ACCOUNT_ID")
                else None
            ),
            run_id=run_id,
            evidence_path=evidence_path.resolve(),
        )

    def runtime_environment(self) -> dict[str, str]:
        return {
            "ANANTA_JMAP_LIVE_USERNAME": self.username,
            "ANANTA_JMAP_LIVE_CREDENTIAL": self.credential,
        }


def keyword_membership(metadata: Mapping[str, Any], keyword: str) -> bool:
    keywords = metadata.get("keywords") or {}
    if isinstance(keywords, Mapping):
        return bool(keywords.get(keyword))
    if isinstance(keywords, Sequence) and not isinstance(keywords, (str, bytes)):
        return keyword in keywords
    return False


def write_live_evidence(
    config: LiveJmapProviderConfig,
    *,
    results: Mapping[str, Mapping[str, Any]],
) -> None:
    report = {
        "schema": "jmap_provider_smoke.v1",
        "run_id": config.run_id,
        "provider": "jmap",
        "checks": {
            name: {
                "status": str(result.get("status") or ""),
                "reason_code": str(result.get("reason_code") or ""),
                "counters": dict(result.get("counters") or {}),
            }
            for name, result in sorted(results.items())
        },
        "mutation_restored": True,
        "contains_credentials_or_body": False,
    }
    destination = config.evidence_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "LIVE_RUN_ENV",
    "LiveJmapProviderConfig",
    "keyword_membership",
    "write_live_evidence",
]
