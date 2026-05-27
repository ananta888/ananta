"""ProposalService — generates HeuristicProposals from DecisionTrace evidence.

Proposals are saved to heuristics/candidates/ only — never to active/.
Only anonymised trace IDs (no raw content) appear in trace_evidence.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agent.services.heuristic_runtime.decision_trace import DecisionTrace
from agent.services.heuristic_runtime.proposal_validator import HeuristicProposal


_CANDIDATES_SUBDIR = "candidates"
_DEFAULT_BASE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "heuristics")
)


@dataclass
class ProposalGenerationResult:
    proposal: HeuristicProposal
    dominant_heuristic_id: str
    fallback_pattern: str
    trace_count: int


class ProposalService:
    def __init__(self, base_path: str | None = None) -> None:
        self._base_path = base_path or _DEFAULT_BASE_PATH

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
        for t in traces:
            if t.heuristic_id:
                heuristic_counter[t.heuristic_id] += 1
            if t.fallback_reason:
                fallback_counter[t.fallback_reason] += 1

        dominant_id = heuristic_counter.most_common(1)[0][0] if heuristic_counter else "unknown"
        dominant_fallback = fallback_counter.most_common(1)[0][0] if fallback_counter else "no_match"

        trace_evidence = [t.event_id for t in traces[:20]]  # max 20 anonymised IDs

        ttl_expired_count = sum(
            1 for t in traces
            if t.fallback_reason and ("ttl" in t.fallback_reason or "expired" in t.fallback_reason)
        )
        ai_timeout_count = sum(1 for t in traces if t.fallback_reason == "ai_timeout")

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
            },
        )
        return ProposalGenerationResult(
            proposal=proposal,
            dominant_heuristic_id=dominant_id,
            fallback_pattern=dominant_fallback,
            trace_count=len(traces),
        )

    def save_candidate(self, proposal: HeuristicProposal) -> str:
        """Write proposal JSON to heuristics/candidates/. Returns file path."""
        candidates_dir = os.path.join(self._base_path, _CANDIDATES_SUBDIR)
        os.makedirs(candidates_dir, exist_ok=True)

        filename = f"{proposal.proposal_id}.json"
        file_path = os.path.join(candidates_dir, filename)

        data: dict[str, Any] = proposal.to_dict()
        data["saved_at"] = time.time()
        data["status"] = "candidate"

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return file_path

    def list_candidates(self) -> list[dict[str, Any]]:
        """Return all proposals in heuristics/candidates/."""
        candidates_dir = os.path.join(self._base_path, _CANDIDATES_SUBDIR)
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
