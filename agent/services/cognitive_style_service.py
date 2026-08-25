"""Hub-owned cognitive-style profiles, targets, overlays and governance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
from threading import RLock
from typing import Iterable, Protocol

from agent.services.model_profile_loader import ModelProfile
from agent.services.model_selection_service import CognitiveStyleFitPolicy
from ananta_contracts.cognitive_style import (
    CognitiveStyleConfiguration,
    CognitiveStyleMutationCommand,
    CognitiveStylePersistedState,
    CognitiveStyleReadModel,
    RoleStyleOverlay,
    StyleBenchmarkResult,
    StyleEvolutionProposal,
    StyleEvolutionTransitionCommand,
    StyleMismatchEvidence,
    TeamStyleDiversityReport,
    TeamStyleMember,
)
from ananta_contracts.model_selection import (
    AgentStyleProfile,
    CognitiveStyleVector,
    RoleStyleTarget,
    StyleRange,
)

HEURISTIC_NOTICE = (
    "Cognitive-Style-Werte sind operative, benchmarkbasierte Heuristiken für "
    "Modell- und Rollenrouting; sie sind keine psychologische Diagnose und "
    "verleihen niemals Berechtigungen."
)


class CognitiveStyleConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__("cognitive_style_revision_conflict")


class CognitiveStyleStatePort(Protocol):
    def load(self) -> CognitiveStylePersistedState: ...
    def save_if_revision(
        self, expected_revision: int, state: CognitiveStylePersistedState
    ) -> bool: ...


class InMemoryCognitiveStyleStateRepository:
    def __init__(self, state: CognitiveStylePersistedState | None = None) -> None:
        self._state = state or CognitiveStylePersistedState(
            configuration=CognitiveStyleConfiguration(revision=0)
        )
        self._lock = RLock()

    def load(self) -> CognitiveStylePersistedState:
        with self._lock:
            return self._state

    def save_if_revision(
        self, expected_revision: int, state: CognitiveStylePersistedState
    ) -> bool:
        with self._lock:
            if self._state.configuration.revision != expected_revision:
                return False
            self._state = state
            return True


def standard_role_style_targets() -> tuple[RoleStyleTarget, ...]:
    def target(
        role: str,
        rule: tuple[float, float, float],
        truth: tuple[float, float, float],
        initiative: tuple[float, float, float],
        rationale: str,
    ) -> RoleStyleTarget:
        return RoleStyleTarget(
            target_id=f"standard.{role}.v1",
            role_id=role,
            rule_correctness=StyleRange(
                minimum=rule[0], maximum=rule[1], weight=rule[2]
            ),
            truth_exploration=StyleRange(
                minimum=truth[0], maximum=truth[1], weight=truth[2]
            ),
            initiative_assertiveness=StyleRange(
                minimum=initiative[0], maximum=initiative[1], weight=initiative[2]
            ),
            rationale=rationale,
        )

    return (
        target("developer", (.75, 1, 2), (.35, .75, 1), (.3, .7, 1), "Reproduzierbare Umsetzung mit begrenzter Exploration."),
        target("implementer", (.8, 1, 2), (.3, .7, 1), (.3, .65, 1), "Definition-of-Done und Verträge stehen im Vordergrund."),
        target("qa", (.8, 1, 2), (.65, 1, 2), (.25, .7, 1), "Korrektheit plus aktive Suche nach Gegenbeispielen."),
        target("verifier", (.85, 1, 2), (.65, 1, 2), (.2, .65, 1), "Evidenzbasierte, reproduzierbare Verifikation."),
        target("researcher", (.35, .8, 1), (.8, 1, 3), (.4, .8, 1), "Prämissen, Evidenz und Alternativerklärungen explorieren."),
        target("architect", (.5, .9, 1), (.75, 1, 3), (.45, .85, 1), "Systemische Annahmen und langfristige Folgen prüfen."),
        target("reviewer", (.65, 1, 2), (.75, 1, 3), (.35, .8, 1), "Korrektheit prüfen und schwache Prämissen offenlegen."),
        target("challenger", (.3, .8, 1), (.8, 1, 3), (.75, 1, 2), "Begründete Gegenpositionen innerhalb expliziter Grenzen."),
        target("red_team", (.45, .9, 1), (.8, 1, 3), (.7, 1, 2), "Risiken und Gegenbeispiele aktiv suchen, ohne Rechte auszuweiten."),
        target("product", (.45, .85, 1), (.55, .9, 2), (.5, .85, 2), "Balanciert Zielklarheit, Lernen und Entscheidung."),
        target("planner", (.55, .9, 2), (.55, .9, 2), (.4, .8, 1), "Umsetzbare Pläne mit Prämissenprüfung."),
        target("scrum_master", (.4, .8, 1), (.45, .85, 2), (.4, .8, 2), "Facilitation, Goal-Fokus und Anpassungsfähigkeit statt Starrheit."),
        target("coordinator", (.5, .85, 1), (.45, .8, 1), (.45, .8, 2), "Koordination bleibt an Ziele und Hub-Grenzen gebunden."),
        target("coder", (.75, 1, 2), (.35, .75, 1), (.3, .7, 1), "Technischer Alias für Implementierungsrollen."),
        target("reasoning", (.4, .85, 1), (.75, 1, 3), (.35, .8, 1), "Generische explorative Reasoning-Rolle."),
        target("chat", (.4, .85, 1), (.4, .85, 1), (.35, .8, 1), "Ausgewogene interaktive Assistenz."),
    )


def standard_role_style_overlays() -> tuple[RoleStyleOverlay, ...]:
    return (
        RoleStyleOverlay(
            overlay_id="standard.rule-checklist.v1", role_id="implementer", revision=1,
            reinforces=("rule_correctness",),
            instruction=(
                "Arbeite die geltenden Verträge und Akzeptanzkriterien als Checkliste ab. "
                "Schließe mit einer überprüfbaren Definition-of-Done; zusätzliche Aktionen "
                "bedürfen weiterhin der vorhandenen Berechtigung."
            ),
        ),
        RoleStyleOverlay(
            overlay_id="standard.evidence-review.v1", role_id="reviewer", revision=1,
            reinforces=("truth_exploration",),
            instruction=(
                "Trenne Evidenz von Vermutung, benenne mindestens eine Gegenhypothese und "
                "prüfe die stärkste alternative Erklärung."
            ),
        ),
        RoleStyleOverlay(
            overlay_id="standard.bounded-challenger.v1", role_id="challenger", revision=1,
            reinforces=("truth_exploration", "initiative_assertiveness"),
            instruction=(
                "Sprich schwache Annahmen früh an und formuliere eine Alternative als Proposal. "
                "Führe ohne Freigabe keine zusätzlichen Änderungen oder Tools aus."
            ),
        ),
    )


class CognitiveStyleService:
    """Coordinates state transitions; workers receive projections only."""

    _TRANSITIONS = {
        "proposed": {"validated", "rejected"},
        "validated": {"approved", "rejected"},
        "approved": {"applied", "rejected"},
        "applied": {"measuring", "rolled_back"},
        "measuring": {"rolled_back", "rejected"},
        "rolled_back": set(),
        "rejected": set(),
    }

    def __init__(self, repository: CognitiveStyleStatePort) -> None:
        self._repository = repository

    def read(self) -> CognitiveStyleReadModel:
        state = self._with_defaults(self._repository.load())
        return CognitiveStyleReadModel(
            configuration=state.configuration,
            profile_history=state.profile_history,
            mismatch_evidence=state.mismatch_evidence,
            evolution_proposals=state.evolution_proposals,
            heuristic_notice=HEURISTIC_NOTICE,
        )

    def mutate(self, command: CognitiveStyleMutationCommand) -> CognitiveStyleConfiguration:
        current = self._with_defaults(self._repository.load())
        if current.configuration.revision != command.expected_revision:
            raise CognitiveStyleConflict(current.configuration.revision)
        model_profile_ids = [item.model_profile_id for item in command.profiles]
        if len(model_profile_ids) != len(set(model_profile_ids)):
            raise ValueError("active_style_model_profile_duplicate")
        configuration = CognitiveStyleConfiguration(
            revision=command.expected_revision + 1,
            profiles=command.profiles,
            role_targets=command.role_targets,
            overlays=command.overlays,
        )
        updated = current.model_copy(update={"configuration": configuration})
        self._save(command.expected_revision, updated)
        return configuration

    def record_benchmark_result(
        self,
        result: StyleBenchmarkResult,
        *,
        expected_revision: int,
    ) -> CognitiveStyleConfiguration:
        current = self._with_defaults(self._repository.load())
        if current.configuration.revision != expected_revision:
            raise CognitiveStyleConflict(current.configuration.revision)
        # Routing must have one unambiguous active style vector per model. A
        # changed model/runtime/prompt/sampling context starts a new measurement,
        # but the previous measurement remains auditable in history.
        same_model = lambda item: (
            item.model_profile_id == result.profile.model_profile_id
        )
        replaced = tuple(
            item for item in current.configuration.profiles if same_model(item)
        )
        profiles = tuple(
            item for item in current.configuration.profiles if not same_model(item)
        ) + (result.profile,)
        configuration = current.configuration.model_copy(update={
            "revision": expected_revision + 1,
            "profiles": profiles,
        })
        updated = current.model_copy(update={
            "configuration": configuration,
            "profile_history": tuple((*current.profile_history, *replaced))[-1000:],
        })
        self._save(expected_revision, updated)
        return configuration

    def record_mismatch(
        self,
        evidence: StyleMismatchEvidence,
        *,
        expected_revision: int,
    ) -> CognitiveStyleReadModel:
        current = self._with_defaults(self._repository.load())
        if current.configuration.revision != expected_revision:
            raise CognitiveStyleConflict(current.configuration.revision)
        configuration = current.configuration.model_copy(
            update={"revision": expected_revision + 1}
        )
        updated = current.model_copy(update={
            "configuration": configuration,
            "mismatch_evidence": tuple((*current.mismatch_evidence, evidence))[-2000:],
        })
        self._save(expected_revision, updated)
        return self.read()

    def add_proposal(
        self,
        proposal: StyleEvolutionProposal,
        *,
        expected_revision: int,
    ) -> CognitiveStyleReadModel:
        current = self._with_defaults(self._repository.load())
        if current.configuration.revision != expected_revision:
            raise CognitiveStyleConflict(current.configuration.revision)
        if any(item.proposal_id == proposal.proposal_id for item in current.evolution_proposals):
            raise ValueError("style_evolution_proposal_duplicate")
        configuration = current.configuration.model_copy(
            update={"revision": expected_revision + 1}
        )
        updated = current.model_copy(update={
            "configuration": configuration,
            "evolution_proposals": (*current.evolution_proposals, proposal),
        })
        self._save(expected_revision, updated)
        return self.read()

    def transition_proposal(
        self,
        proposal_id: str,
        command: StyleEvolutionTransitionCommand,
        *,
        expected_revision: int,
    ) -> CognitiveStyleReadModel:
        current = self._with_defaults(self._repository.load())
        if current.configuration.revision != expected_revision:
            raise CognitiveStyleConflict(current.configuration.revision)
        proposal = next(
            (item for item in current.evolution_proposals if item.proposal_id == proposal_id),
            None,
        )
        if proposal is None:
            raise ValueError("style_evolution_proposal_not_found")
        if proposal.status != command.expected_status:
            raise ValueError("style_evolution_status_conflict")
        if command.target_status not in self._TRANSITIONS.get(proposal.status, set()):
            raise ValueError("style_evolution_transition_invalid")
        if command.target_status in {"approved", "applied"} and not command.review_reference:
            raise ValueError("style_evolution_review_required")
        transitioned = proposal.model_copy(update={"status": command.target_status})
        proposals = tuple(
            transitioned if item.proposal_id == proposal_id else item
            for item in current.evolution_proposals
        )
        configuration = current.configuration.model_copy(
            update={"revision": expected_revision + 1}
        )
        updated = current.model_copy(update={
            "configuration": configuration,
            "evolution_proposals": proposals,
        })
        self._save(expected_revision, updated)
        return self.read()

    def resolve_target(
        self,
        role_id: str,
        *,
        organization_id: str | None = None,
        project_id: str | None = None,
    ) -> RoleStyleTarget | None:
        targets = [
            item for item in self.read().configuration.role_targets
            if item.role_id == role_id
        ]
        precedence = (
            (project_id, lambda item: item.project_id == project_id),
            (organization_id, lambda item: item.organization_id == organization_id and item.project_id is None),
            (True, lambda item: item.organization_id is None and item.project_id is None),
        )
        for active, predicate in precedence:
            if active:
                target = next((item for item in targets if predicate(item)), None)
                if target is not None:
                    return target
        return None

    def overlay_instruction(self, role_id: str) -> str | None:
        overlay = next(
            (
                item for item in self.read().configuration.overlays
                if item.role_id == role_id and item.enabled
            ),
            None,
        )
        return overlay.instruction if overlay is not None else None

    def diversity(self, members: Iterable[TeamStyleMember]) -> TeamStyleDiversityReport:
        values = tuple(members)
        active = {
            item.model_profile_id: item
            for item in self.read().configuration.profiles
        }
        vectors = [active[item.model_profile_id].scores for item in values if item.model_profile_id in active]
        if len(vectors) < 2:
            return TeamStyleDiversityReport(
                members_evaluated=len(vectors), centroid={}, spread={},
                classification="insufficient_data",
                warnings=("style_diversity_requires_two_profiles",),
            )
        dimensions = (
            "rule_correctness", "truth_exploration", "initiative_assertiveness"
        )
        centroid = {
            name: round(sum(getattr(item, name) for item in vectors) / len(vectors), 6)
            for name in dimensions
        }
        spread = {
            name: round(sqrt(sum(
                (getattr(item, name) - centroid[name]) ** 2 for item in vectors
            ) / len(vectors)), 6)
            for name in dimensions
        }
        maximum = max(centroid, key=centroid.get)
        if max(spread.values()) < .08:
            classification = "homogeneous"
        elif max(centroid.values()) - min(centroid.values()) < .15:
            classification = "balanced"
        else:
            classification = {
                "rule_correctness": "rule_oriented",
                "truth_exploration": "exploratory",
                "initiative_assertiveness": "initiative_oriented",
            }[maximum]
        warnings = (
            ("style_team_homogeneous",) if classification == "homogeneous" else ()
        )
        complementary = (
            ("reviewer", "challenger") if centroid["truth_exploration"] < .55 else ()
        )
        return TeamStyleDiversityReport(
            members_evaluated=len(vectors), centroid=centroid, spread=spread,
            classification=classification, warnings=warnings,
            complementary_role_ids=complementary,
            capability_or_security_overridden=False,
        )

    def _save(self, expected_revision: int, state: CognitiveStylePersistedState) -> None:
        if not self._repository.save_if_revision(expected_revision, state):
            raise CognitiveStyleConflict(
                self._repository.load().configuration.revision
            )

    @staticmethod
    def _with_defaults(state: CognitiveStylePersistedState) -> CognitiveStylePersistedState:
        configuration = state.configuration
        if not configuration.role_targets:
            configuration = configuration.model_copy(update={
                "role_targets": standard_role_style_targets()
            })
        if not configuration.overlays:
            configuration = configuration.model_copy(update={
                "overlays": standard_role_style_overlays()
            })
        return state.model_copy(update={"configuration": configuration})


@dataclass(frozen=True, slots=True)
class StyleRank:
    profile: ModelProfile
    score: float | None
    confidence: float | None
    reason: str


class StyleRoutingObserver(Protocol):
    def record(self, outcome: str) -> None: ...


class PrometheusStyleRoutingObserver:
    """Bounded-label metrics adapter kept outside the ranking calculation."""

    def record(self, outcome: str) -> None:
        from agent import metrics

        metrics.AGENT_STYLE_ROUTING_DECISIONS_TOTAL.labels(outcome=outcome).inc()


class CognitiveStyleRankingPolicy:
    """Soft ranking only; candidates must already pass all hard gates."""

    def __init__(
        self,
        *,
        profiles: Iterable[AgentStyleProfile],
        targets: Iterable[RoleStyleTarget],
        weight: float = 0.25,
        stale_after_days: int = 90,
        observer: StyleRoutingObserver | None = None,
    ) -> None:
        self._profiles = tuple(profiles)
        self._targets = tuple(targets)
        self._weight = max(0.0, min(1.0, float(weight)))
        self._stale_after_days = max(1, stale_after_days)
        self._fit = CognitiveStyleFitPolicy()
        self._observer = observer or PrometheusStyleRoutingObserver()

    def rank_profiles(
        self,
        candidates: tuple[ModelProfile, ...],
        *,
        role_id: str,
        project_id: str | None = None,
        organization_id: str | None = None,
    ) -> tuple[StyleRank, ...]:
        if not candidates:
            self._observer.record("no_candidates")
            return ()
        target = self._target(role_id, project_id, organization_id)
        if self._weight <= 0:
            self._observer.record("ranking_disabled")
            return tuple(StyleRank(item, None, None, "style_ranking_disabled") for item in candidates)
        if target is None:
            self._observer.record("target_unavailable")
            return tuple(StyleRank(item, None, None, "style_target_unavailable") for item in candidates)
        base_index = {item.profile_id: index for index, item in enumerate(candidates)}
        ranks: list[StyleRank] = []
        for candidate in candidates:
            profile = self._latest_profile(candidate.profile_id)
            if profile is None:
                ranks.append(StyleRank(candidate, None, None, "style_profile_unavailable"))
                continue
            decision = self._fit.evaluate(profile, target)
            confidence = self._effective_confidence(profile)
            score = round(decision.score * confidence / max(profile.confidence, 1e-9), 6)
            ranks.append(StyleRank(candidate, score, confidence, "style_fit_applied"))
        self._observer.record(
            "applied" if any(item.score is not None for item in ranks)
            else "profile_unavailable"
        )
        return tuple(sorted(ranks, key=lambda item: (
            -(self._weight * (item.score if item.score is not None else -1)),
            base_index[item.profile.profile_id],
        )))

    def _target(
        self, role_id: str, project_id: str | None, organization_id: str | None
    ) -> RoleStyleTarget | None:
        matches = [item for item in self._targets if item.role_id == role_id]
        return (
            next((item for item in matches if project_id and item.project_id == project_id), None)
            or next((item for item in matches if organization_id and item.organization_id == organization_id and item.project_id is None), None)
            or next((item for item in matches if item.project_id is None and item.organization_id is None), None)
        )

    def _latest_profile(self, model_profile_id: str) -> AgentStyleProfile | None:
        matches = [
            item for item in self._profiles if item.model_profile_id == model_profile_id
        ]
        return max(matches, key=lambda item: item.measured_at, default=None)

    def _effective_confidence(self, profile: AgentStyleProfile) -> float:
        try:
            measured = datetime.fromisoformat(profile.measured_at.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - measured).total_seconds() / 86400
        except ValueError:
            return 0.0
        freshness = max(0.0, 1.0 - age_days / self._stale_after_days)
        return round(profile.confidence * freshness, 6)


def get_cognitive_style_service() -> CognitiveStyleService:
    from agent.repositories.cognitive_style_state import (
        SqlCognitiveStyleStateRepository,
    )

    return CognitiveStyleService(SqlCognitiveStyleStateRepository())


def get_cognitive_style_ranking_policy(
    *,
    weight: float = .25,
) -> CognitiveStyleRankingPolicy:
    read = get_cognitive_style_service().read()
    return CognitiveStyleRankingPolicy(
        profiles=read.configuration.profiles,
        targets=read.configuration.role_targets,
        weight=weight,
    )


__all__ = [
    "CognitiveStyleConflict", "CognitiveStyleRankingPolicy",
    "CognitiveStyleService", "CognitiveStyleStatePort", "HEURISTIC_NOTICE",
    "InMemoryCognitiveStyleStateRepository", "PrometheusStyleRoutingObserver",
    "StyleRank", "StyleRoutingObserver",
    "get_cognitive_style_ranking_policy", "get_cognitive_style_service", "standard_role_style_overlays",
    "standard_role_style_targets",
]
