from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Dict

from agent.db_models import MemoryEntryDB
from agent.repository import memory_entry_repo
from worker.core.context_access_policy import (
    ContextAccessPolicy,
    ContextAccessPolicyEvaluator,
    DestinationContext,
    RequestedOperation,
    Decision,
    ModelScope,
    Sensitivity,
    SourceType
)

_POLICY_VERSION = "memory_policy_v2"

# ── MemoryProposalArtifact (AWF-T024) ─────────────────────────────────────────

@dataclass
class MemoryProposalArtifact:
    """Proposed long-term memory entry awaiting Hub approval. AWF-T024.

    Workers emit proposals; Hub decides whether to promote to trusted memory.
    """
    title: str
    rationale: str
    evidence_refs: list[str] = field(default_factory=list)
    proposed_scope: str = "project"
    sensitivity: str = "internal"
    confidence: float = 0.5
    approval_required: bool = True
    proposed_by: str = ""
    approved: bool = False


def normalize_result_memory_policy(value: dict | None) -> dict[str, object]:
    """T022: normalize memory policy with sensitivity, redaction, TTL, and scope controls."""
    payload = value if isinstance(value, dict) else {}

    def _positive_int(raw, default: int, *, minimum: int = 1, maximum: int = 100000) -> int:
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def _positive_float_or_none(raw) -> float | None:
        if raw is None:
            return None
        try:
            v = float(raw)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    return {
        "enabled": bool(payload.get("enabled", True)),
        "create_followup_artifact": bool(payload.get("create_followup_artifact", True)),
        "retrieval_document_max_chars": _positive_int(payload.get("retrieval_document_max_chars"), 2200, minimum=400, maximum=12000),
        "raw_history_max_chars": _positive_int(payload.get("raw_history_max_chars"), 12000, minimum=1000, maximum=100000),
        "archive_raw_output": bool(payload.get("archive_raw_output", False)),
        "neighbor_file_terms_enabled": bool(payload.get("neighbor_file_terms_enabled", True)),
        # T022: sensitivity + redaction
        "sensitivity": str(payload.get("sensitivity") or "internal").strip().lower(),
        "redact_before_persist": bool(payload.get("redact_before_persist", True)),
        "policy_version": _POLICY_VERSION,
        # T023: memory scopes
        "default_memory_scope": str(payload.get("default_memory_scope") or "task").strip().lower(),
        "allowed_scopes": list(payload.get("allowed_scopes") or ["session", "task", "project"]),
        # T026: TTL
        "default_ttl_seconds": _positive_float_or_none(payload.get("default_ttl_seconds")),
        "retention_class": str(payload.get("retention_class") or "standard").strip().lower(),
    }


class ResultMemoryService:
    """Persists worker results as hub-owned memory entries for later retrieval."""

    _FILE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|java|go|rs|md|json|yaml|yml|xml))(?![A-Za-z0-9_./-])")

    def __init__(
        self,
        *,
        memory_entry_repository: Any = None,
        memory_tree_ingestion_service: Any = None,
        auto_ingest_tree: bool = False,
    ) -> None:
        # Capture the explicit override (or sentinel) so that test-DI works.
        # The default case (``None``) defers to ``_memory_entry_repo`` which
        # resolves ``agent.services.di.memory_entry_repo`` at call time.
        # This eliminates the module-import-cache footgun: even if a test
        # monkeypatches ``agent.services.di.memory_entry_repo`` AFTER this
        # service is constructed, the call-time resolution sees the patch.
        self._memory_entry_repository_override = memory_entry_repository
        self._memory_tree_ingestion_svc = memory_tree_ingestion_service
        self._auto_ingest_tree = auto_ingest_tree

    @property
    def _memory_entry_repo(self) -> Any:
        """Call-time repository resolution.

        SOLID: DIP — depend on the abstraction (the ``di`` module's
        factory layer), not the module-level import cache. The factory
        re-reads ``agent.services.di.memory_entry_repo`` on every call,
        so test patches (and any future repository rebinding) are seen.
        """
        if self._memory_entry_repository_override is not None:
            return self._memory_entry_repository_override
        from agent.services.di import get_memory_entry_repository
        return get_memory_entry_repository()

    def _compact_output(self, output: str) -> dict[str, object]:
        text = str(output or "").strip()
        if not text:
            return {"summary": None, "compacted_summary": None, "bullet_points": []}
        normalized = re.sub(r"\s+", " ", text).strip()
        lines = [line.strip(" -\t") for line in text.splitlines() if line.strip()]
        bullets = []
        for line in lines:
            lowered = line.lower()
            if any(marker in lowered for marker in ("fix", "added", "changed", "updated", "removed", "test", "verify", "result")):
                bullets.append(line[:180])
            if len(bullets) >= 5:
                break
        if not bullets:
            bullets = [segment.strip()[:180] for segment in re.split(r"[.;]", normalized) if segment.strip()][:4]
        summary = normalized[:280]
        compacted = " | ".join(bullets)[:900]
        return {
            "summary": summary or None,
            "compacted_summary": compacted or None,
            "bullet_points": bullets,
        }

    def _extract_changed_files(self, text: str) -> list[str]:
        files: list[str] = []
        for match in self._FILE_PATH_RE.findall(text or ""):
            path = str(match[0] if isinstance(match, tuple) else match).strip()
            if path and path not in files:
                files.append(path)
            if len(files) >= 8:
                break
        return files

    def _structured_summary(self, *, output: str, compacted_summary: str, bullets: list[str]) -> dict[str, object]:
        normalized = re.sub(r"\s+", " ", str(output or "")).strip()
        lowered = normalized.lower()
        status = "completed"
        if any(token in lowered for token in ("failed", "exception", "traceback", "error", "regression")):
            status = "attention_needed"

        changed_files = self._extract_changed_files(output)
        test_pass = any(token in lowered for token in ("test passed", "tests passed", "all tests passed", "passed"))
        test_fail = any(token in lowered for token in ("test failed", "tests failed", "failing test", "failed"))
        next_steps: list[str] = []
        risks: list[str] = []
        for line in [line.strip() for line in str(output or "").splitlines() if line.strip()]:
            line_l = line.lower()
            if any(marker in line_l for marker in ("next step", "todo", "follow-up", "followup")):
                next_steps.append(line[:200])
            if any(marker in line_l for marker in ("risk", "warning", "blocked", "pending", "todo", "fixme")):
                risks.append(line[:200])
            if len(next_steps) >= 4 and len(risks) >= 4:
                break

        focus_terms: list[str] = []
        for value in [*changed_files, *(bullets or [])]:
            for token in re.findall(r"[A-Za-z0-9_]+", str(value or "").lower()):
                if len(token) < 4:
                    continue
                if token not in focus_terms:
                    focus_terms.append(token)
                if len(focus_terms) >= 16:
                    break
            if len(focus_terms) >= 16:
                break

        return {
            "status": status,
            "changed_files": changed_files,
            "tests": {
                "passed_signal": test_pass,
                "failed_signal": test_fail,
            },
            "next_steps": next_steps[:4],
            "risks": risks[:4],
            "focus_terms": focus_terms,
            "compacted_summary": str(compacted_summary or "").strip() or None,
        }

    def _build_retrieval_document(
        self,
        *,
        summary: str | None,
        bullets: list[str],
        structured: dict[str, object],
        max_chars: int = 2200,
    ) -> str:
        lines: list[str] = []
        if summary:
            lines.append(f"summary: {summary}")
        status = str(structured.get("status") or "").strip()
        if status:
            lines.append(f"status: {status}")
        changed_files = [str(item).strip() for item in list(structured.get("changed_files") or []) if str(item).strip()]
        if changed_files:
            lines.append(f"changed_files: {', '.join(changed_files)}")
        tests = dict(structured.get("tests") or {})
        lines.append(
            f"tests: passed_signal={bool(tests.get('passed_signal'))}; failed_signal={bool(tests.get('failed_signal'))}"
        )
        risks = [str(item).strip() for item in list(structured.get("risks") or []) if str(item).strip()]
        if risks:
            lines.append("risks: " + " | ".join(risks[:4]))
        next_steps = [str(item).strip() for item in list(structured.get("next_steps") or []) if str(item).strip()]
        if next_steps:
            lines.append("next_steps: " + " | ".join(next_steps[:4]))
        if bullets:
            lines.append("highlights: " + " | ".join(str(item) for item in bullets[:6]))
        return "\n".join(line for line in lines if line).strip()[: max(400, int(max_chars or 2200))]

    def record_worker_result_memory(
        self,
        *,
        task_id: str | None,
        goal_id: str | None,
        trace_id: str | None,
        worker_job_id: str | None,
        title: str | None,
        output: str | None,
        artifact_refs: list[dict] | None = None,
        retrieval_tags: list[str] | None = None,
        metadata: dict | None = None,
        policy: dict | None = None,
        # T023: memory scope
        memory_scope: str | None = None,
        # T025: provenance
        generated_by: str | None = None,
        confidence: float = 1.0,
        approved: bool = False,
        context_access_policy: ContextAccessPolicy | None = None, # CAP-BE-T025
    ) -> MemoryEntryDB | None:
        """Persist worker result memory. Returns None if policy disables write. T022–T026."""
        memory_policy = normalize_result_memory_policy(policy)

        # CAP-BE-T025: Memory-Policy-Enforcement
        if context_access_policy:
             evaluator = ContextAccessPolicyEvaluator(context_access_policy)
             block_metadata = {
                 "source_type": SourceType.memory,
                 "source_ref": title or "unknown_memory",
                 "sensitivity": Sensitivity(memory_policy.get("sensitivity", "unknown"))
             }
             dest = DestinationContext(
                 worker_id=generated_by or "hub",
                 worker_kind="native",
                 runtime_target_id="hub",
                 runtime_kind="local",
                 provider_id="hub",
                 provider_location="local",
                 model_id="none",
                 model_scope=ModelScope.none,
                 cloud_effective=False,
                 external_effective=False,
                 local_effective=True,
                 requested_operation=RequestedOperation.memory_write
             )
             decision = evaluator.get_decision(block_metadata, dest)
             if decision.decision == Decision.deny:
                 # Deny persistence if policy forbids memory write for this sensitivity/source
                 return None

        # T022: honor enabled=False
        if not bool(memory_policy["enabled"]):
            return None

        raw_output = str(output or "")

        # T022: redact secrets before any persistence
        redaction_applied = False
        if bool(memory_policy["redact_before_persist"]):
            from worker.core.redaction import redact_text
            raw_output_for_storage = redact_text(raw_output)
            redaction_applied = raw_output_for_storage != raw_output
        else:
            raw_output_for_storage = raw_output

        compact = self._compact_output(raw_output_for_storage)
        summary = str(compact.get("summary") or "") or (raw_output_for_storage[:280] if raw_output_for_storage else None)
        structured = self._structured_summary(
            output=raw_output_for_storage,
            compacted_summary=str(compact.get("compacted_summary") or ""),
            bullets=list(compact.get("bullet_points") or []),
        )
        retrieval_document = self._build_retrieval_document(
            summary=summary,
            bullets=list(compact.get("bullet_points") or []),
            structured=structured,
            max_chars=int(memory_policy["retrieval_document_max_chars"]),
        )

        # T023: resolve scope
        scope = str(memory_scope or memory_policy["default_memory_scope"]).strip().lower() or "task"
        allowed_scopes = list(memory_policy["allowed_scopes"])
        if scope not in allowed_scopes:
            scope = str(memory_policy["default_memory_scope"])

        # T026: TTL / expires_at
        default_ttl = memory_policy["default_ttl_seconds"]
        expires_at: float | None = None
        if default_ttl:
            expires_at = time.time() + float(default_ttl)

        base_metadata = dict(metadata or {})
        followup_artifact = {
            "kind": "task_result_summary",
            "task_id": task_id,
            "goal_id": goal_id,
            "trace_id": trace_id,
            "summary": summary,
            "structured_summary": structured,
            "retrieval_tags": list(retrieval_tags or []),
        } if bool(memory_policy["create_followup_artifact"]) else None

        entry = self._memory_entry_repo.save(
            MemoryEntryDB(
                task_id=task_id,
                goal_id=goal_id,
                trace_id=trace_id,
                worker_job_id=worker_job_id,
                entry_type="worker_result",
                title=title,
                summary=summary,
                content=(retrieval_document or str(compact.get("compacted_summary") or "").strip() or raw_output_for_storage or None),
                artifact_refs=list(artifact_refs or []),
                retrieval_tags=list(retrieval_tags or []),
                memory_metadata={
                    **base_metadata,
                    "followup_artifact": followup_artifact,
                    "compacted_summary": compact.get("compacted_summary"),
                    "bullet_points": list(compact.get("bullet_points") or []),
                    "structured_summary": structured,
                    "retrieval_document": retrieval_document or None,
                    "memory_format": "worker_result_compact_v3",
                    "compaction_policy": memory_policy,
                    "raw_history": raw_output_for_storage[: int(memory_policy["raw_history_max_chars"])] if bool(memory_policy["archive_raw_output"]) else None,
                    "original_output_chars": len(raw_output),
                    # T022: policy tracking
                    "policy_version": memory_policy["policy_version"],
                    "redaction_applied": redaction_applied,
                    "sensitivity": memory_policy["sensitivity"],
                    # T023: scope
                    "memory_scope": scope,
                    # T025: provenance
                    "generated_by": str(generated_by or task_id or ""),
                    "approved": bool(approved),
                    "confidence": float(confidence),
                    "trust_source": "worker_result",
                    # T026: TTL
                    "expires_at": expires_at,
                    "retention_class": memory_policy["retention_class"],
                },
            )
        )

        # OHA-012: optional MemoryTree ingest after successful persist
        if entry is not None and self._auto_ingest_tree and self._memory_tree_ingestion_svc is not None:
            self._try_memory_tree_ingest(
                entry=entry,
                sensitivity=memory_policy["sensitivity"],
                approved=bool(approved),
                goal_id=goal_id,
                task_id=task_id,
                summary=summary,
                retrieval_document=retrieval_document,
                generated_by=generated_by,
            )

        return entry

    def _try_memory_tree_ingest(
        self,
        *,
        entry: Any,
        sensitivity: str,
        approved: bool,
        goal_id: str | None,
        task_id: str | None,
        summary: str | None,
        retrieval_document: str | None,
        generated_by: str | None,
    ) -> None:
        """Ingest a persisted memory entry as a MemoryTree leaf. OHA-012."""
        try:
            source_id = f"result_memory:{goal_id or task_id or 'unknown'}"
            content = retrieval_document or summary or ""
            if not content.strip():
                return
            # Trusted leaves come from approved results; untrusted from worker proposals
            kind = "trusted_leaf" if approved else "untrusted_leaf"
            self._memory_tree_ingestion_svc._store.ingest_chunk(
                source_id=source_id,
                source_type="result_memory",
                label=str(getattr(entry, "title", None) or task_id or "result")[:256],
                content=content[:4000],
                scope="task",
                kind=kind,
                sensitivity=sensitivity,
                provenance_ref=f"memory_entry:{getattr(entry, 'id', '')}",
                created_by=generated_by or task_id or "",
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "ResultMemoryService: MemoryTree ingest failed — %s", exc
            )

    def build_memory_proposal(
        self,
        *,
        title: str,
        rationale: str,
        evidence_refs: list[str] | None = None,
        proposed_scope: str = "project",
        sensitivity: str = "internal",
        confidence: float = 0.5,
        proposed_by: str = "",
    ) -> MemoryProposalArtifact:
        """Build a MemoryProposalArtifact without writing to DB. AWF-T024.

        Workers propose durable memory; Hub decides whether to promote.
        """
        return MemoryProposalArtifact(
            title=str(title or "").strip(),
            rationale=str(rationale or "").strip(),
            evidence_refs=list(evidence_refs or []),
            proposed_scope=str(proposed_scope or "project").strip().lower(),
            sensitivity=str(sensitivity or "internal").strip().lower(),
            confidence=float(confidence),
            approval_required=True,
            proposed_by=str(proposed_by or "").strip(),
            approved=False,
        )


# Lazy module-global service. Tests must call ``get_result_memory_service()``
# so that the per-call ``ResultMemoryService()`` construction picks up any
# monkeypatched ``agent.services.di.memory_entry_repo`` via the property
# lookup. Do NOT cache the instance at import time — that would freeze the
# repository reference and reintroduce the cross-file-order footgun.
result_memory_service: Optional["ResultMemoryService"] = None


def get_result_memory_service() -> "ResultMemoryService":
    """Return the per-process ``ResultMemoryService``.

    A fresh instance is constructed on every call so the property-based
    ``_memory_entry_repo`` lookup sees the *current* value of
    ``agent.services.di.memory_entry_repo``. Tests can patch
    ``agent.services.di.memory_entry_repo`` and the next call to
    ``get_result_memory_service()`` will see it.

    The previous module-level instance variable
    (``result_memory_service = ResultMemoryService(...)``) was removed
    because it captured the import-time singleton and made property
    lookups resolve to a frozen reference. See plan:
    ``docs/plans/2026-06-20-di-adapter-layer-cross-file-isolation.md``.
    """
    return ResultMemoryService()
