"""Candidate migration gate (ASH-016).

At startup, candidates with simulation_result: null are not suitable for
active use (default_policy.requires_simulation: true). This module:
  1. Scans all candidate files in heuristics/candidates/<domain>/
  2. Sets status: "pending_simulation" on candidates with simulation_result: null
  3. Candidates with status "pending_simulation" are excluded from active selection
  4. Migration is idempotent (no-op on already-migrated candidates)

Also handles ASH-014 TTL expiry: expired candidates are quarantined.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any


_log = logging.getLogger(__name__)

_DEFAULT_BASE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "heuristics")
)

# Statuses that are safe for runtime use
_ACTIVE_STATUSES = frozenset({"candidate", "auto_promoted"})


@dataclass
class MigrationReport:
    domain: str
    total_scanned: int = 0
    set_pending_simulation: int = 0
    quarantined_expired: int = 0
    already_pending: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "total_scanned": self.total_scanned,
            "set_pending_simulation": self.set_pending_simulation,
            "quarantined_expired": self.quarantined_expired,
            "already_pending": self.already_pending,
            "errors": list(self.errors),
        }


def run_candidate_migration(
    domain: str = "tui_snake",
    base_path: str | None = None,
) -> MigrationReport:
    """Scan candidates/<domain>/ and migrate stale/pending candidates.

    Idempotent — safe to call on every startup.
    """
    root = base_path or _DEFAULT_BASE_PATH
    candidates_dir = os.path.join(root, "candidates", domain)
    quarantine_dir = os.path.join(root, "quarantine", domain)

    report = MigrationReport(domain=domain)

    if not os.path.isdir(candidates_dir):
        return report

    now = time.time()

    for fname in sorted(os.listdir(candidates_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(candidates_dir, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            report.errors.append(f"{fname}: read error: {exc}")
            continue

        report.total_scanned += 1
        changed = False
        current_status = str(data.get("status") or "candidate")

        # ASH-014: quarantine expired candidates
        expires_at = data.get("expires_at")
        if expires_at is not None and float(expires_at) < now:
            os.makedirs(quarantine_dir, exist_ok=True)
            dst = os.path.join(quarantine_dir, fname)
            data["status"] = "quarantined"
            data["quarantine_reason"] = "ttl_expired"
            data["quarantined_at"] = now
            try:
                with open(dst, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.remove(fpath)
                report.quarantined_expired += 1
                _log.info("candidate %s quarantined: ttl_expired", fname)
            except OSError as exc:
                report.errors.append(f"{fname}: quarantine error: {exc}")
            continue

        # ASH-016: mark simulation_result: null as pending_simulation
        if current_status == "pending_simulation":
            report.already_pending += 1
            continue

        if data.get("simulation_result") is None and current_status in _ACTIVE_STATUSES:
            data["status"] = "pending_simulation"
            changed = True
            report.set_pending_simulation += 1
            _log.info("candidate %s → pending_simulation (simulation_result is null)", fname)

        if changed:
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except OSError as exc:
                report.errors.append(f"{fname}: write error: {exc}")

    if report.total_scanned:
        _log.info(
            "candidate migration [%s]: scanned=%d pending_sim=%d quarantined=%d",
            domain,
            report.total_scanned,
            report.set_pending_simulation,
            report.quarantined_expired,
        )

    return report


def is_candidate_eligible(candidate: dict[str, Any]) -> bool:
    """Return True if a candidate may be used for active snake operation.

    Excludes: pending_simulation, quarantined, failed.
    """
    status = str(candidate.get("status") or "candidate")
    return status in _ACTIVE_STATUSES
