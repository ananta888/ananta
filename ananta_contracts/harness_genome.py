"""Canonical, versioned Harness Genome contract (SEH-002).

A genome describes one deterministic agent-control configuration: prompt
strategy, context/memory/retrieval policy, tool policy, agent topology, model
routing, reducer/compaction, verification and gate references. It is fully
serialisable, diffable and content-addressed: two semantically identical
genomes produce the same ``genome_id``.

Boundaries (SEH-003): the genome only *references* immutable gates, secret
stores and the security boundary. Raw secrets, benchmark holdouts, evaluators,
budget caps and promotion authority are never part of a genome.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

HARNESS_GENOME_SCHEMA = "ananta.harness-genome.v1"
SUPPORTED_GENOME_SCHEMAS = (HARNESS_GENOME_SCHEMA,)

_DIGEST = re.compile(r"[0-9a-f]{64}")
_SECRET_REF = re.compile(r"[a-z][a-z0-9_]*://[A-Za-z0-9_./:-]{1,200}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _digest(value: Any, reason: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(reason)
    return value


def _identifier(value: Any, reason: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(reason)
    return value


def _identifier_tuple(value: Any, reason: str, *, unique: bool = True) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(reason)
    items = tuple(_identifier(item, reason) for item in value)
    if unique and len(set(items)) != len(items):
        raise ValueError(reason)
    return items


def _bounded_int(value: Any, reason: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(reason)
    return value


def _ratio(value: Any, reason: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(reason)
    return float(value)


# ── Sections ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PromptStrategy:
    system_ref: str
    user_ref: str
    repair_ref: str | None = None
    few_shot_refs: tuple[str, ...] = ()
    output_profile: str = "default"

    def to_mapping(self) -> dict[str, Any]:
        _identifier(self.system_ref, "harness_prompt_system_ref_invalid")
        _identifier(self.user_ref, "harness_prompt_user_ref_invalid")
        if self.repair_ref is not None:
            _identifier(self.repair_ref, "harness_prompt_repair_ref_invalid")
        _identifier_tuple(self.few_shot_refs, "harness_prompt_few_shot_refs_invalid")
        _identifier(self.output_profile, "harness_prompt_output_profile_invalid")
        return {"system_ref": self.system_ref, "user_ref": self.user_ref, "repair_ref": self.repair_ref,
                "few_shot_refs": list(self.few_shot_refs), "output_profile": self.output_profile}


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    budget_tokens: int
    reserved_completion_tokens: int
    reserved_tool_tokens: int = 0
    compaction_threshold: float = 0.8
    observation_retention: str = "bounded"
    evidence_preserving_reduction: bool = True
    retrieval_window_tokens: int = 4096

    def to_mapping(self) -> dict[str, Any]:
        _bounded_int(self.budget_tokens, "harness_context_budget_invalid", minimum=1024, maximum=8_388_608)
        reserved = (_bounded_int(self.reserved_completion_tokens, "harness_context_reserved_completion_invalid",
                                 minimum=1, maximum=1_048_576)
                    + _bounded_int(self.reserved_tool_tokens, "harness_context_reserved_tool_invalid",
                                   minimum=0, maximum=1_048_576))
        if reserved >= self.budget_tokens:
            raise ValueError("harness_context_reserved_exceeds_budget")
        _ratio(self.compaction_threshold, "harness_context_compaction_threshold_invalid")
        _identifier(self.observation_retention, "harness_context_observation_retention_invalid")
        _bounded_int(self.retrieval_window_tokens, "harness_context_retrieval_window_invalid",
                     minimum=256, maximum=8_388_608)
        return {"budget_tokens": self.budget_tokens, "reserved_completion_tokens": self.reserved_completion_tokens,
                "reserved_tool_tokens": self.reserved_tool_tokens,
                "compaction_threshold": self.compaction_threshold,
                "observation_retention": self.observation_retention,
                "evidence_preserving_reduction": bool(self.evidence_preserving_reduction),
                "retrieval_window_tokens": self.retrieval_window_tokens}


@dataclass(frozen=True, slots=True)
class MemoryPolicy:
    scopes: tuple[str, ...] = ("task",)
    retention_class: str = "standard"
    recall_strategy: str = "policy_gateway"

    def to_mapping(self) -> dict[str, Any]:
        _identifier_tuple(self.scopes, "harness_memory_scopes_invalid")
        _identifier(self.retention_class, "harness_memory_retention_invalid")
        _identifier(self.recall_strategy, "harness_memory_recall_invalid")
        return {"scopes": list(self.scopes), "retention_class": self.retention_class,
                "recall_strategy": self.recall_strategy}


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    top_k: int = 8
    candidate_budget: int = 64
    dense_weight: float = 1.0
    bm25_weight: float = 0.0
    graph_weight: float = 0.0
    reranking: str = "none"
    query_rewrite: str = "none"
    profile: str = "default"

    def to_mapping(self) -> dict[str, Any]:
        _bounded_int(self.top_k, "harness_retrieval_top_k_invalid", minimum=1, maximum=1000)
        _bounded_int(self.candidate_budget, "harness_retrieval_candidate_budget_invalid", minimum=1, maximum=100_000)
        for name in ("dense_weight", "bm25_weight", "graph_weight"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 100.0:
                raise ValueError(f"harness_retrieval_{name}_invalid")
        _identifier(self.reranking, "harness_retrieval_reranking_invalid")
        _identifier(self.query_rewrite, "harness_retrieval_query_rewrite_invalid")
        _identifier(self.profile, "harness_retrieval_profile_invalid")
        return {"top_k": self.top_k, "candidate_budget": self.candidate_budget,
                "dense_weight": float(self.dense_weight), "bm25_weight": float(self.bm25_weight),
                "graph_weight": float(self.graph_weight), "reranking": self.reranking,
                "query_rewrite": self.query_rewrite, "profile": self.profile}


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    allowed_tools: tuple[str, ...]
    routing_heuristic: str = "contract"
    action_fusion: bool = False
    parallel_safe_tools: bool = True
    retry_max: int = 1
    backoff_ms: int = 0
    timeout_ms: int = 30_000

    def to_mapping(self) -> dict[str, Any]:
        _identifier_tuple(self.allowed_tools, "harness_tools_allowlist_invalid")
        _identifier(self.routing_heuristic, "harness_tools_routing_invalid")
        _bounded_int(self.retry_max, "harness_tools_retry_invalid", minimum=0, maximum=10)
        _bounded_int(self.backoff_ms, "harness_tools_backoff_invalid", minimum=0, maximum=60_000)
        _bounded_int(self.timeout_ms, "harness_tools_timeout_invalid", minimum=100, maximum=3_600_000)
        return {"allowed_tools": list(self.allowed_tools), "routing_heuristic": self.routing_heuristic,
                "action_fusion": bool(self.action_fusion), "parallel_safe_tools": bool(self.parallel_safe_tools),
                "retry_max": self.retry_max, "backoff_ms": self.backoff_ms, "timeout_ms": self.timeout_ms}


@dataclass(frozen=True, slots=True)
class AgentTopology:
    pattern: str = "single"
    roles: tuple[str, ...] = ()
    delegation_depth: int = 0
    fan_out: int = 0
    termination: str = "task_terminal"

    def to_mapping(self) -> dict[str, Any]:
        _identifier(self.pattern, "harness_topology_pattern_invalid")
        _identifier_tuple(self.roles, "harness_topology_roles_invalid")
        _bounded_int(self.delegation_depth, "harness_topology_delegation_depth_invalid", minimum=0, maximum=8)
        _bounded_int(self.fan_out, "harness_topology_fan_out_invalid", minimum=0, maximum=64)
        _identifier(self.termination, "harness_topology_termination_invalid")
        if self.delegation_depth == 0 and self.fan_out != 0:
            raise ValueError("harness_topology_fan_out_without_delegation")
        return {"pattern": self.pattern, "roles": list(self.roles), "delegation_depth": self.delegation_depth,
                "fan_out": self.fan_out, "termination": self.termination}


@dataclass(frozen=True, slots=True)
class ModelRouting:
    provider_id: str
    model_id: str
    fallback_chain: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        _identifier(self.provider_id, "harness_routing_provider_invalid")
        _identifier(self.model_id, "harness_routing_model_invalid")
        _identifier_tuple(self.fallback_chain, "harness_routing_fallback_invalid")
        return {"provider_id": self.provider_id, "model_id": self.model_id,
                "fallback_chain": list(self.fallback_chain)}


@dataclass(frozen=True, slots=True)
class ReducerPolicy:
    strategy: str = "none"
    preserve_evidence: bool = True
    verify_fallback: bool = True
    max_ratio: float = 1.0

    def to_mapping(self) -> dict[str, Any]:
        _identifier(self.strategy, "harness_reducer_strategy_invalid")
        _ratio(self.max_ratio, "harness_reducer_max_ratio_invalid")
        return {"strategy": self.strategy, "preserve_evidence": bool(self.preserve_evidence),
                "verify_fallback": bool(self.verify_fallback), "max_ratio": self.max_ratio}


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    strategy: str = "hub_gate"
    require_evidence: bool = True

    def to_mapping(self) -> dict[str, Any]:
        _identifier(self.strategy, "harness_verification_strategy_invalid")
        return {"strategy": self.strategy, "require_evidence": bool(self.require_evidence)}


@dataclass(frozen=True, slots=True)
class GatePolicy:
    """References to immutable gates. A genome can never disable or inline a gate."""

    gate_refs: tuple[str, ...]
    promotion_ref: str

    def to_mapping(self) -> dict[str, Any]:
        _identifier_tuple(self.gate_refs, "harness_gate_refs_invalid")
        _identifier(self.promotion_ref, "harness_gate_promotion_ref_invalid")
        if not self.gate_refs:
            raise ValueError("harness_gate_refs_required")
        return {"gate_refs": list(self.gate_refs), "promotion_ref": self.promotion_ref}


_SECTION_TYPES: dict[str, type] = {
    "prompt": PromptStrategy, "context": ContextPolicy, "memory": MemoryPolicy,
    "retrieval": RetrievalPolicy, "tools": ToolPolicy, "topology": AgentTopology,
    "model_routing": ModelRouting, "reducer": ReducerPolicy, "verification": VerificationPolicy,
    "gates": GatePolicy,
}
_SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "prompt": ("system_ref", "user_ref", "repair_ref", "few_shot_refs", "output_profile"),
    "context": ("budget_tokens", "reserved_completion_tokens", "reserved_tool_tokens", "compaction_threshold",
                "observation_retention", "evidence_preserving_reduction", "retrieval_window_tokens"),
    "memory": ("scopes", "retention_class", "recall_strategy"),
    "retrieval": ("top_k", "candidate_budget", "dense_weight", "bm25_weight", "graph_weight", "reranking",
                  "query_rewrite", "profile"),
    "tools": ("allowed_tools", "routing_heuristic", "action_fusion", "parallel_safe_tools", "retry_max",
              "backoff_ms", "timeout_ms"),
    "topology": ("pattern", "roles", "delegation_depth", "fan_out", "termination"),
    "model_routing": ("provider_id", "model_id", "fallback_chain"),
    "reducer": ("strategy", "preserve_evidence", "verify_fallback", "max_ratio"),
    "verification": ("strategy", "require_evidence"),
    "gates": ("gate_refs", "promotion_ref"),
}
_TUPLE_SECTIONS = {"prompt": ("few_shot_refs",), "memory": ("scopes",), "tools": ("allowed_tools",),
                   "topology": ("roles",), "model_routing": ("fallback_chain",), "gates": ("gate_refs",)}


@dataclass(frozen=True, slots=True)
class HarnessGenome:
    code_hash: str
    config_hash: str
    prompt: PromptStrategy
    context: ContextPolicy
    tools: ToolPolicy
    model_routing: ModelRouting
    gates: GatePolicy
    memory: MemoryPolicy = field(default_factory=MemoryPolicy)
    retrieval: RetrievalPolicy = field(default_factory=RetrievalPolicy)
    topology: AgentTopology = field(default_factory=AgentTopology)
    reducer: ReducerPolicy = field(default_factory=ReducerPolicy)
    verification: VerificationPolicy = field(default_factory=VerificationPolicy)
    secret_refs: tuple[str, ...] = ()
    parents: tuple[str, ...] = ()
    mutation_operators: tuple[str, ...] = ()
    created_from: str | None = None
    schema: str = HARNESS_GENOME_SCHEMA

    # ── identity / serialisation ─────────────────────────────────────────────
    def semantic_mapping(self) -> dict[str, Any]:
        """The content that defines semantic identity (no provenance)."""
        return {
            "schema": self.schema,
            "code_hash": _digest(self.code_hash, "harness_code_hash_invalid"),
            "config_hash": _digest(self.config_hash, "harness_config_hash_invalid"),
            "secret_refs": list(self._validated_secret_refs()),
            **{name: getattr(self, name).to_mapping() for name in _SECTION_TYPES},
        }

    def _validated_secret_refs(self) -> tuple[str, ...]:
        refs = _identifier_tuple(self.secret_refs, "harness_secret_refs_invalid")
        for ref in refs:
            if _SECRET_REF.fullmatch(ref) is None:
                raise ValueError("harness_secret_ref_not_a_reference")
        return refs

    @property
    def genome_id(self) -> str:
        return content_digest(self.semantic_mapping())

    def to_mapping(self) -> dict[str, Any]:
        payload = self.semantic_mapping()
        payload.update({
            "genome_id": self.genome_id,
            "parents": list(_identifier_tuple(self.parents, "harness_parents_invalid")),
            "mutation_operators": list(_identifier_tuple(self.mutation_operators, "harness_mutations_invalid")),
            "created_from": self.created_from,
        })
        return payload

    def to_json(self) -> str:
        return canonical_json(self.to_mapping())

    def diff(self, other: "HarnessGenome") -> dict[str, Any]:
        """Path -> (left, right) for every differing semantic field."""
        left, right = self.semantic_mapping(), other.semantic_mapping()
        changes: dict[str, Any] = {}
        for key in sorted(set(left) | set(right)):
            a, b = left.get(key), right.get(key)
            if isinstance(a, Mapping) and isinstance(b, Mapping):
                for sub in sorted(set(a) | set(b)):
                    if a.get(sub) != b.get(sub):
                        changes[f"{key}.{sub}"] = [a.get(sub), b.get(sub)]
            elif a != b:
                changes[key] = [a, b]
        return changes

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "HarnessGenome":
        payload = migrate_genome_mapping(dict(payload))
        sections: dict[str, Any] = {}
        for name, section_type in _SECTION_TYPES.items():
            raw = payload.get(name)
            if not isinstance(raw, Mapping):
                raise ValueError(f"harness_section_missing:{name}")
            fields = {}
            for field_name in _SECTION_FIELDS[name]:
                value = raw.get(field_name)
                if name in _TUPLE_SECTIONS and field_name in _TUPLE_SECTIONS[name] and isinstance(value, list):
                    value = tuple(value)
                fields[field_name] = value
            sections[name] = section_type(**fields)
        genome = cls(
            code_hash=payload.get("code_hash"), config_hash=payload.get("config_hash"),
            secret_refs=tuple(payload.get("secret_refs") or ()),
            parents=tuple(payload.get("parents") or ()),
            mutation_operators=tuple(payload.get("mutation_operators") or ()),
            created_from=payload.get("created_from"), **sections,
        )
        expected = payload.get("genome_id")
        if expected is not None and expected != genome.genome_id:
            raise ValueError("harness_genome_id_mismatch")
        return genome

    def assert_valid(self) -> "HarnessGenome":
        self.semantic_mapping()
        return self


_MIGRATIONS: dict[str, Any] = {}


def migrate_genome_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the registered schema migrations up to the current version."""
    schema = payload.get("schema")
    if schema is None:
        raise ValueError("harness_genome_schema_missing")
    seen: set[str] = set()
    while schema != HARNESS_GENOME_SCHEMA:
        if schema in seen:
            raise ValueError("harness_genome_migration_cycle")
        seen.add(schema)
        migration = _MIGRATIONS.get(schema)
        if migration is None:
            raise ValueError(f"harness_genome_schema_unsupported:{schema}")
        payload = migration(payload)
        schema = payload.get("schema")
    return payload
