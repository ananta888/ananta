"""Deterministic, telemetry-grounded proposals for the admitted mutation surface."""

from __future__ import annotations

import json
import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from ananta_contracts.harness_genome import HarnessGenome, canonical_json, content_digest

_MAX_SIGNAL_FILES = 10_000_000
_MAX_MODULE_COUNT = 32
_MAX_MODULE_CHARS = 200


class CodeCompassSignalsPort(Protocol):
    """Bounded, hub-side CodeCompass observations for hypothesis grounding."""

    def collect(self) -> "CodeCompassSignals | None": ...


@dataclass(frozen=True, slots=True)
class CodeCompassSignals:
    """What the CodeCompass graph can honestly tell the search, nothing more.

    ``indexed_files``/``indexed_edges`` are graph coverage counts;
    ``architecture_modules`` are the most-populated second-level path modules.
    These are observations, not SRC_/RUN_ evidence identities.
    """

    indexed_files: int = 0
    indexed_edges: int = 0
    architecture_modules: tuple[str, ...] = ()

    def __post_init__(self):
        for value in (self.indexed_files, self.indexed_edges):
            if type(value) is not int or not 0 <= value <= _MAX_SIGNAL_FILES:
                raise ValueError("harness_codecompass_signals_invalid")
        modules = tuple(dict.fromkeys(self.architecture_modules))
        if len(modules) > _MAX_MODULE_COUNT or any(
            not isinstance(module, str) or not module.strip() or len(module) > _MAX_MODULE_CHARS for module in modules
        ):
            raise ValueError("harness_codecompass_signals_invalid")
        object.__setattr__(self, "architecture_modules", modules)

    @property
    def present(self) -> bool:
        return self.indexed_files > 0 or self.indexed_edges > 0


@dataclass(frozen=True, slots=True)
class HarnessTelemetry:
    truncation_events: int = 0
    safe_fusion_opportunities: int = 0
    codecompass: CodeCompassSignals | None = field(default=None)

    def __post_init__(self):
        for value in (self.truncation_events, self.safe_fusion_opportunities):
            if type(value) is not int or not 0 <= value <= 1_000_000:
                raise ValueError("harness_telemetry_invalid")
        if self.codecompass is not None and not isinstance(self.codecompass, CodeCompassSignals):
            raise ValueError("harness_telemetry_invalid")


@dataclass(frozen=True, slots=True)
class HarnessHypothesis:
    operator_id: str
    params_json: str
    rationale: str
    priority: int

    @property
    def idea_key(self) -> str:
        # This is a proposal content hash, never a SRC_/RUN_ evidence identity.
        return content_digest({"operator": self.operator_id, "params": json.loads(self.params_json)})

    def to_mapping(self) -> dict:
        return {
            "idea_key": self.idea_key,
            "operator_id": self.operator_id,
            "params": json.loads(self.params_json),
            "rationale": self.rationale,
            "priority": self.priority,
        }


class HarnessHypothesisGenerator:
    def propose(
        self,
        genome: HarnessGenome,
        telemetry: HarnessTelemetry,
        *,
        context_budgets: tuple[int, ...],
        model_context_limit: int,
    ) -> tuple[HarnessHypothesis, ...]:
        genome.assert_valid()
        if type(model_context_limit) is not int or model_context_limit < 1024:
            raise ValueError("harness_model_context_limit_invalid")
        if any(type(value) is not int or not 1024 <= value <= 131072 for value in context_budgets):
            raise ValueError("harness_context_search_space_invalid")
        ideas = []
        if telemetry.truncation_events:
            for budget in sorted(set(context_budgets)):
                if genome.context.budget_tokens < budget <= model_context_limit:
                    ideas.append(
                        HarnessHypothesis(
                            "widen_context",
                            canonical_json({"budget_tokens": budget}),
                            f"{telemetry.truncation_events} truncations: evaluate context budget {budget}",
                            budget,
                        )
                    )
        if telemetry.safe_fusion_opportunities and not genome.tools.action_fusion and genome.tools.allowed_tools:
            ideas.append(
                HarnessHypothesis(
                    "enable_action_fusion",
                    "{}",
                    f"{telemetry.safe_fusion_opportunities} admitted safe fusion opportunities",
                    200000,
                )
            )
        ideas.extend(self._codecompass_ideas(genome, telemetry.codecompass))
        return tuple(ideas)

    @staticmethod
    def _codecompass_ideas(genome: HarnessGenome, signals: CodeCompassSignals | None) -> list[HarnessHypothesis]:
        """Ground retrieval mutations in actual graph coverage, never in optimism."""
        if signals is None or not signals.present:
            return []
        ideas: list[HarnessHypothesis] = []
        if signals.indexed_files >= 8 and genome.retrieval.graph_weight < 1.0:
            target = min(1.0, round(genome.retrieval.graph_weight + 0.5, 4))
            if target > genome.retrieval.graph_weight:
                ideas.append(
                    HarnessHypothesis(
                        "boost_graph_retrieval",
                        canonical_json({"graph_weight": target}),
                        f"CodeCompass graph covers {signals.indexed_files} files and "
                        f"{len(signals.architecture_modules)} modules: raise graph retrieval weight to {target}",
                        150000,
                    )
                )
        if signals.indexed_edges >= 16 and (
            genome.retrieval.top_k < 1000 or genome.retrieval.candidate_budget < 100000
        ):
            params = {
                "top_k": min(1000, max(genome.retrieval.top_k * 2, 16)),
                "candidate_budget": min(100000, max(genome.retrieval.candidate_budget * 2, 128)),
            }
            ideas.append(
                HarnessHypothesis(
                    "deepen_retrieval",
                    canonical_json(params),
                    f"CodeCompass graph exposes {signals.indexed_edges} relations: "
                    f"evaluate top_k {params['top_k']} with candidate budget {params['candidate_budget']}",
                    140000,
                )
            )
        return ideas

    @staticmethod
    def choose(
        ideas: tuple[HarnessHypothesis, ...], *, seed: int, attempt: int, exploration_rate: float
    ) -> tuple[HarnessHypothesis, str]:
        if not ideas:
            raise ValueError("harness_hypotheses_empty")
        ordered = sorted(ideas, key=lambda idea: (idea.priority, idea.idea_key))
        rng = random.Random(content_digest({"seed": seed, "attempt": attempt}))
        if rng.random() < exploration_rate:
            return rng.choice(ordered), "explore"
        return ordered[0], "exploit"


class CodeCompassSignalCollector:
    """Derive bounded architecture/coverage signals from a CodeCompass graph store.

    Best effort by contract: a store that cannot be read yields ``None`` so the
    search continues on its ordinary telemetry instead of failing the session.
    """

    def __init__(self, store: Protocol):
        self._store = store

    def collect(self) -> CodeCompassSignals | None:
        try:
            payload = self._store.load()
        except Exception:
            return None
        if not isinstance(payload, Mapping):
            return None
        nodes = payload.get("nodes")
        edges = payload.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            return None
        modules: dict[str, int] = {}
        files = 0
        for node in nodes[:20000]:
            if not isinstance(node, dict):
                continue
            path = str(node.get("path") or "")
            if not path:
                continue
            files += 1
            parts = path.split("/")
            if len(parts) >= 2:
                modules[parts[0] + "/" + parts[1]] = modules.get(parts[0] + "/" + parts[1], 0) + 1
        top_modules = tuple(sorted(modules, key=lambda name: (-modules[name], name))[:_MAX_MODULE_COUNT])
        return CodeCompassSignals(
            indexed_files=files, indexed_edges=min(len(edges), _MAX_SIGNAL_FILES), architecture_modules=top_modules
        )
