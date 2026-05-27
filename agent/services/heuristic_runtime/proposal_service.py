"""ProposalService — generates HeuristicProposals from DecisionTrace evidence.

Proposals are saved to heuristics/candidates/<domain>/ (ASH-010 domain subdirs).
Only anonymised trace IDs (no raw content) appear in trace_evidence.

ASH-011: fingerprint = SHA-256 over (domain, base_heuristic_ref, action_kind, parameters_canonical_json).
         Duplicate proposals increment evidence_count instead of creating a new file.
ASH-012: extended metrics block (trace_count, evidence_count, dominant_fallback_reason, …).
ASH-014: expires_at = saved_at + candidate_ttl_days * 86400 (default: 14 days).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agent.services.heuristic_runtime.decision_trace import DecisionTrace
from agent.services.heuristic_runtime.proposal_validator import HeuristicProposal


_DEFAULT_BASE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "heuristics")
)
_DEFAULT_TTL_DAYS = 14
_PROVENANCE_MAX = 20


# ── Fingerprint ───────────────────────────────────────────────────────────────

def compute_candidate_fingerprint(
    domain: str,
    base_heuristic_ref: str | None,
    action_kind: str,
    parameters: dict[str, Any],
) -> str:
    """SHA-256 fingerprint over stable, content-specific fields (ASH-011).

    fallback_reason and context_hash_pattern are intentionally excluded
    (too variable; belong in metrics, not fingerprint).
    """
    canonical = json.dumps(
        {
            "domain": domain,
            "base_heuristic_ref": base_heuristic_ref or "",
            "action_kind": action_kind,
            "parameters_canonical_json": json.dumps(parameters, sort_keys=True),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class ProposalGenerationResult:
    proposal: HeuristicProposal
    dominant_heuristic_id: str
    fallback_pattern: str
    trace_count: int


# ── Service ───────────────────────────────────────────────────────────────────

class ProposalService:
    def __init__(self, base_path: str | None = None, ttl_days: int = _DEFAULT_TTL_DAYS) -> None:
        self._base_path = base_path or _DEFAULT_BASE_PATH
        self._ttl_days = ttl_days

    def _candidates_dir(self, domain: str) -> str:
        """Return domain-scoped candidates directory (ASH-010)."""
        return os.path.join(self._base_path, "candidates", domain)

    def generate_from_traces(
        self,
        traces: list[DecisionTrace],
        *,
        proposed_by: str = "ananta-worker",
        domain: str = "tui_snake",
    ) -> ProposalGenerationResult:
        """Analyse traces and produce an improvement proposal.

        The proposal references only anonymised trace IDs — no raw text content.
        """
        if not traces:
            raise ValueError("generate_from_traces: traces must not be empty")

        heuristic_counter: Counter[str] = Counter()
        fallback_counter: Counter[str] = Counter()
        action_counter: Counter[str] = Counter()
        duration_ms_list: list[float] = []

        for t in traces:
            if t.heuristic_id:
                heuristic_counter[t.heuristic_id] += 1
            if t.fallback_reason:
                fallback_counter[t.fallback_reason] += 1
            if t.action_kind:
                action_counter[t.action_kind] += 1
            if t.duration_ms is not None:
                duration_ms_list.append(t.duration_ms)

        dominant_id = heuristic_counter.most_common(1)[0][0] if heuristic_counter else "unknown"
        dominant_fallback = fallback_counter.most_common(1)[0][0] if fallback_counter else "no_match"
        dominant_action = action_counter.most_common(1)[0][0] if action_counter else "follow_with_distance"

        trace_evidence = [t.event_id for t in traces[:_PROVENANCE_MAX]]

        ttl_expired_count = sum(
            1 for t in traces
            if t.fallback_reason and ("ttl" in t.fallback_reason or "expired" in t.fallback_reason)
        )
        ai_timeout_count = sum(1 for t in traces if t.fallback_reason == "ai_timeout")
        no_trigger_match_count = sum(1 for t in traces if t.fallback_reason == "no_trigger_match")
        avg_decision_ms = (sum(duration_ms_list) / len(duration_ms_list)) if duration_ms_list else 0.0

        rationale_parts = [f"Dominant fallback pattern: {dominant_fallback}."]
        if ttl_expired_count:
            rationale_parts.append(f"{ttl_expired_count} TTL expiry events detected.")
        if ai_timeout_count:
            rationale_parts.append(f"{ai_timeout_count} AI timeout events detected.")
        rationale = " ".join(rationale_parts)[:500]

        expected_improvement = (
            f"Reduce {dominant_fallback} events by ≥20% over next 50 decisions."
        )

        risks = [
            f"New heuristic may over-generalise for domain {domain}.",
            "Insufficient test coverage for edge cases.",
        ]
        required_tests = [
            f"test_{dominant_id.replace('-', '_')}_handles_{dominant_fallback}",
            f"test_{dominant_id.replace('-', '_')}_ttl_boundary",
            "test_policy_violations_zero",
        ]

        # ASH-012: extended metrics block
        metrics: dict[str, Any] = {
            "trace_count": len(traces),
            "evidence_count": 1,
            "dominant_fallback_reason": dominant_fallback,
            "ai_timeout_count": ai_timeout_count,
            "ttl_expired_count": ttl_expired_count,
            "no_trigger_match_count": no_trigger_match_count,
            "average_decision_duration_ms": round(avg_decision_ms, 2),
            "user_positive_feedback_count": 0,
            "user_negative_feedback_count": 0,
            "provenance_trace_ids": trace_evidence,
        }

        proposal = HeuristicProposal(
            proposal_id=str(uuid.uuid4()),
            proposed_by=proposed_by,
            domain=domain,
            strategy_kind="follow",
            description=f"Improvement proposal for {dominant_id}: {rationale[:100]}",
            capabilities=["read_local_context", "read_active_task"],
            requested_ttl_seconds=7.0 if "snake" in domain else 15.0,
            safety_class="bounded",
            deterministic=True,
            base_heuristic_ref=dominant_id,
            simulation_result=None,
            human_approval_ref=None,
            version="1.0.0",
            parameters={
                "rationale": rationale,
                "expected_improvement": expected_improvement,
                "risks": risks,
                "required_tests": required_tests,
                "trace_evidence": trace_evidence,
                "action_kind": dominant_action,
            },
        )
        proposal._metrics = metrics  # carry metrics for save_candidate
        return ProposalGenerationResult(
            proposal=proposal,
            dominant_heuristic_id=dominant_id,
            fallback_pattern=dominant_fallback,
            trace_count=len(traces),
        )

    def save_candidate(self, proposal: HeuristicProposal) -> str:
        """Write proposal JSON to heuristics/candidates/<domain>/.

        ASH-011: if a file with the same fingerprint already exists,
                 evidence_count is incremented instead of creating a new file.
        ASH-014: expires_at = saved_at + ttl_days * 86400.
        Returns the file path (new or existing).
        """
        candidates_dir = self._candidates_dir(proposal.domain)
        os.makedirs(candidates_dir, exist_ok=True)

        params = dict(proposal.parameters)
        action_kind = str(params.get("action_kind") or "follow_with_distance")
        fingerprint = compute_candidate_fingerprint(
            domain=proposal.domain,
            base_heuristic_ref=proposal.base_heuristic_ref,
            action_kind=action_kind,
            parameters={k: v for k, v in params.items()
                        if k not in ("trace_evidence", "rationale", "risks", "required_tests")},
        )

        # ASH-011: check for duplicate fingerprint
        existing_path = self._find_by_fingerprint(candidates_dir, fingerprint)
        if existing_path:
            self._merge_evidence(existing_path, proposal)
            return existing_path

        saved_at = time.time()
        expires_at = saved_at + self._ttl_days * 86400  # ASH-014

        metrics: dict[str, Any] = getattr(proposal, "_metrics", {})
        if not metrics:
            params_evidence = params.get("trace_evidence", [])
            metrics = {
                "trace_count": len(params_evidence),
                "evidence_count": 1,
                "dominant_fallback_reason": "",
                "ai_timeout_count": 0,
                "ttl_expired_count": 0,
                "no_trigger_match_count": 0,
                "average_decision_duration_ms": 0.0,
                "user_positive_feedback_count": 0,
                "user_negative_feedback_count": 0,
                "provenance_trace_ids": params_evidence[:_PROVENANCE_MAX],
            }

        data: dict[str, Any] = proposal.to_dict()
        data["saved_at"] = saved_at
        data["expires_at"] = expires_at          # ASH-014
        data["status"] = "candidate"
        data["fingerprint"] = fingerprint        # ASH-011
        data["metrics"] = metrics                # ASH-012

        filename = f"{proposal.proposal_id}.json"
        file_path = os.path.join(candidates_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return file_path

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_by_fingerprint(self, candidates_dir: str, fingerprint: str) -> str | None:
        """Return the path of the first candidate with matching fingerprint, or None."""
        if not os.path.isdir(candidates_dir):
            return None
        for fname in os.listdir(candidates_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(candidates_dir, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("fingerprint") == fingerprint:
                    return path
            except (OSError, json.JSONDecodeError):
                continue
        return None

    def _merge_evidence(self, existing_path: str, proposal: HeuristicProposal) -> None:
        """Increment evidence_count on an existing candidate instead of creating duplicate."""
        try:
            with open(existing_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        metrics = data.get("metrics") or {}
        metrics["evidence_count"] = int(metrics.get("evidence_count", 1)) + 1
        # Merge new provenance IDs (keep max PROVENANCE_MAX)
        existing_ids: list[str] = list(metrics.get("provenance_trace_ids") or [])
        new_ids: list[str] = list(
            (proposal.parameters or {}).get("trace_evidence", [])
        )
        merged = existing_ids + [i for i in new_ids if i not in existing_ids]
        if len(merged) > _PROVENANCE_MAX:
            merged = merged[-_PROVENANCE_MAX:]  # keep most recent
        metrics["provenance_trace_ids"] = merged
        data["metrics"] = metrics
        with open(existing_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def list_candidates(self, domain: str = "tui_snake") -> list[dict[str, Any]]:
        """Return all proposals in heuristics/candidates/<domain>/."""
        candidates_dir = self._candidates_dir(domain)
        if not os.path.isdir(candidates_dir):
            # backwards-compat: also check flat candidates/ dir
            candidates_dir = os.path.join(self._base_path, "candidates")
        if not os.path.isdir(candidates_dir):
            return []
        results = []
        for fname in os.listdir(candidates_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(candidates_dir, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    results.append(json.load(f))
            except (OSError, json.JSONDecodeError):
                pass
        return results
