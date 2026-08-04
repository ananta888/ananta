"""Fail-closed consumption policy for Hub-materialized knowledge indices."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY = (
    "knowledge_index_execution_binding"
)
KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA = (
    "ananta.knowledge_index.materialization-binding.v1"
)
KNOWLEDGE_INDEX_PROJECTED_STATE = "projected"
KNOWLEDGE_INDEX_LEGACY_JOB_SCHEMA = "ananta.knowledge_index_job.v1"
KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA = (
    "ananta.knowledge_index_execution_job.v2"
)
_KNOWLEDGE_INDEX_JOB_ID = re.compile(
    r"^knowledge-index-[0-9a-f]{32}$"
)


@dataclass(frozen=True, slots=True)
class KnowledgeIndexConsumptionDecision:
    """Content-free result of evaluating one index for consumption."""

    allowed: bool
    reason_code: str
    bound_v2: bool


class KnowledgeIndexConsumptionPolicy:
    """Keep provisional v2 outputs outside every consumer boundary.

    Pre-binding legacy indices retain their existing behaviour. Once the
    binding key is present, malformed or non-projected state fails closed;
    the explicit v1 classifier remains compatible after validation.
    Non-artifact v2 indices additionally require an allow-list derived by an
    authenticated Hub boundary; callers must never build that allow-list
    from untrusted tool arguments alone.
    """

    def evaluate(
        self,
        knowledge_index: Any,
        *,
        allowed_index_ids: Collection[str] | None = None,
    ) -> KnowledgeIndexConsumptionDecision:
        metadata = getattr(knowledge_index, "index_metadata", None)
        if not isinstance(metadata, Mapping):
            metadata = {}

        if KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY not in metadata:
            return self._allow("knowledge_index_legacy_compatible", bound_v2=False)

        binding = metadata.get(
            KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY
        )
        if not isinstance(binding, Mapping):
            return self._deny("knowledge_index_binding_invalid")
        if (
            str(binding.get("schema") or "")
            != KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA
        ):
            return self._deny("knowledge_index_binding_invalid")
        if (
            str(binding.get("projection_state") or "")
            != KNOWLEDGE_INDEX_PROJECTED_STATE
        ):
            return self._deny("knowledge_index_projection_not_projected")
        if str(getattr(knowledge_index, "status", "") or "") != "completed":
            return self._deny("knowledge_index_not_completed")

        index_id = str(getattr(knowledge_index, "id", "") or "").strip()
        if (
            not index_id
            or str(binding.get("knowledge_index_id") or "") != index_id
            or _KNOWLEDGE_INDEX_JOB_ID.fullmatch(
                str(binding.get("job_id") or "")
            )
            is None
        ):
            return self._deny("knowledge_index_binding_invalid")

        execution_job_schema = str(
            binding.get("execution_job_schema") or ""
        )
        if execution_job_schema == KNOWLEDGE_INDEX_LEGACY_JOB_SCHEMA:
            return self._allow(
                "knowledge_index_legacy_materialization_compatible",
                bound_v2=False,
            )
        if execution_job_schema != KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA:
            return self._deny("knowledge_index_binding_invalid")
        authority_binding_digest = str(
            binding.get("authority_binding_digest") or ""
        )
        assignment_id = str(binding.get("assignment_id") or "").strip()
        if (
            len(authority_binding_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in authority_binding_digest
            )
            or not assignment_id
        ):
            return self._deny("knowledge_index_binding_invalid")

        source_scope = (
            str(getattr(knowledge_index, "source_scope", "artifact") or "artifact")
            .strip()
            .lower()
            or "artifact"
        )
        if source_scope != "artifact":
            if allowed_index_ids is None or isinstance(
                allowed_index_ids,
                (str, bytes, bytearray),
            ):
                return self._deny("knowledge_index_authorized_scope_required")
            normalized_allowed_ids = {
                str(candidate).strip()
                for candidate in allowed_index_ids
                if str(candidate).strip()
            }
            if not index_id or index_id not in normalized_allowed_ids:
                return self._deny("knowledge_index_not_authorized")

        return self._allow("knowledge_index_consumption_allowed", bound_v2=True)

    def can_consume(
        self,
        knowledge_index: Any,
        *,
        allowed_index_ids: Collection[str] | None = None,
    ) -> bool:
        return self.evaluate(
            knowledge_index,
            allowed_index_ids=allowed_index_ids,
        ).allowed

    @staticmethod
    def _allow(
        reason_code: str,
        *,
        bound_v2: bool,
    ) -> KnowledgeIndexConsumptionDecision:
        return KnowledgeIndexConsumptionDecision(
            allowed=True,
            reason_code=reason_code,
            bound_v2=bound_v2,
        )

    @staticmethod
    def _deny(reason_code: str) -> KnowledgeIndexConsumptionDecision:
        return KnowledgeIndexConsumptionDecision(
            allowed=False,
            reason_code=reason_code,
            bound_v2=True,
        )


knowledge_index_consumption_policy = KnowledgeIndexConsumptionPolicy()


def get_knowledge_index_consumption_policy() -> KnowledgeIndexConsumptionPolicy:
    return knowledge_index_consumption_policy


__all__ = [
    "KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY",
    "KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA",
    "KNOWLEDGE_INDEX_LEGACY_JOB_SCHEMA",
    "KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA",
    "KNOWLEDGE_INDEX_PROJECTED_STATE",
    "KnowledgeIndexConsumptionDecision",
    "KnowledgeIndexConsumptionPolicy",
    "get_knowledge_index_consumption_policy",
]
