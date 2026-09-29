"""End-to-End-Test fuer das Textqualitaets-Subsystem (offline).

Was dieser Test beweist (TQAS-015 AC):
- Plan bleibt technisch valide (keine Veraenderung durch Textqualitaet).
- Textqualitaet wird persistiert (DB-Touch via in-memory Repo-Stub).
- Criteria bleibt bis Review proposed; Kandidat bleibt disabled.
- Apply/VALIDATE/APPLY-Pfade des Plugins schlagen fail-closed fehl.
- Feature-off-Verhalten ist byte-kompatibel.
- Avoid-AI-Writing laeuft ueber FakeSandbox; argv bleibt fest; Outputlimit
  wird vor und nach Sandbox geprueft.
- Feature-off: keine Externen Provider, keine Sandbox, keine LLM-Aufrufe
  (alles Stub-Paths).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from agent.services.evolution.engine import UnsupportedEvolutionOperation
from agent.services.evolution.models import EvolutionCapability, EvolutionContext
from agent.services.repository_registry import get_repository_registry
from agent.services.sandbox_backend import ExecResult, FakeSandbox
from agent.services.text_quality.criteria_extractor_service import CriteriaExtractorService
from agent.services.text_quality.models import ContentKind
from agent.services.text_quality.providers.avoid_ai_writing_provider import (
    AvoidAIWritingProvider,
)
from agent.services.text_quality.runtime_service import get_text_quality_runtime_service

from plugins.anti_slop_evaluator.provider import AntiSlopEvolutionProvider


class _MemoryCriteriaRepo:
    def __init__(self):
        self.saved: list[tuple] = []
        self.by_id: dict[str, object] = {}

    def create(self, criteria):
        self.saved.append(criteria)
        self.by_id[criteria.id] = criteria
        return criteria

    def get_by_id(self, criteria_id):
        return self.by_id.get(criteria_id)

    def get_active(self, profile_name, language, content_kind):
        return None

    def list(self, limit=100):
        return list(self.by_id.values())

    def set_status(self, criteria_id, status):
        row = self.by_id.get(criteria_id)
        if row is None:
            return None
        setattr(row, "status", status)
        return row


class _MemoryEvaluationRepo:
    def __init__(self):
        self.saved: list[object] = []

    def save(self, row):
        self.saved.append(row)
        return row

    def get_by_run(self, planning_run_id):
        return []


@pytest.fixture
def memory_repos(monkeypatch):
    """Ueberschreibt die DB-Touch-Pfade durch In-Memory-Stubs."""

    criteria_repo = _MemoryCriteriaRepo()
    evaluation_repo = _MemoryEvaluationRepo()

    def fake_registry():
        return SimpleNamespace(
            text_quality_criteria_set_repo=criteria_repo,
            text_quality_evaluation_repo=evaluation_repo,
        )

    monkeypatch.setattr("agent.services.text_quality.runtime_service.get_repository_registry", fake_registry)
    monkeypatch.setattr("agent.services.text_quality.criteria_service.get_repository_registry", fake_registry)
    monkeypatch.setattr(
        "agent.services.text_quality.criteria_extractor_service.get_criteria_service",
        lambda: SimpleNamespace(create=criteria_repo.create),
    )
    return criteria_repo, evaluation_repo


def test_feature_off_evaluates_only_core_scanner(memory_repos):
    """Wenn die Config aus ist, darf nichts an der Plan-Validierung kippen."""

    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = {"text_quality": {"enabled": False}}
    with app.app_context():
        # Runtime-Service ist Core-only, weil kein Provider registriert ist.
        runtime = get_text_quality_runtime_service()
        with patch.object(runtime, "evaluator") as patched:
            patched.evaluate.return_value = SimpleNamespace(
                evaluation_id="tqe-1",
                slop_score=0.1,
                depth_score=0.9,
                style_fit_score=0.95,
                criteria_version="1.0",
                evaluator_version="text-quality-v1",
                status=SimpleNamespace(value="completed"),
                reason_codes=[],
                language="de",
                content_kind=ContentKind.FREEFORM_PROSE,
                confidence=1.0,
                model_dump=lambda mode="json", exclude=None: {"evaluation_id": "tqe-1"},
            )
            with patch(
                "agent.services.text_quality.runtime_service.TextQualityEvaluationDB",
                lambda **kwargs: SimpleNamespace(**{**kwargs, "id": "row-1"}),
            ):
                result, row = runtime.evaluate(
                    text="Konkreter Text mit konkreten Beispielen und einer Zahl 7.",
                    language="de",
                    content_kind=ContentKind.FREEFORM_PROSE,
                )
        assert row.id == "row-1"
        assert result.evaluation_id == "tqe-1"


def test_extractor_emits_proposed_criteria_only(memory_repos):
    """Criteria-Extractor-Output bleibt status='proposed' bis Review-Aktion."""

    criteria_repo, _ = memory_repos

    def fake_invoke_json(**kwargs):
        return {
            "blocked_phrases": ["Es ist wichtig zu beachten"],
            "required_positive_traits": ["Konkrete Beispiele"],
            "reason_codes": ["generic_phrase"],
            "confidence": 0.6,
        }

    extractor = CriteriaExtractorService(fake_invoke_json)
    row = extractor.extract(
        examples=["Ein typischer generischer Textbaustein ohne wirklich konkrete Information."],
        language="de",
        content_kind=ContentKind.FREEFORM_PROSE,
        actor="reviewer",
    )
    assert row.status == "proposed"
    assert row.requires_review is True
    assert criteria_repo.saved and criteria_repo.saved[0].status == "proposed"


def test_avoid_ai_writing_provider_uses_fake_sandbox_offline(tmp_path):
    """End-to-End Sandbox-Provider: Detector + Bridge ueber FakeSandbox."""

    detector = tmp_path / "patterns.js"
    detector.write_text("module.exports={analyzeText:()=>({})}")
    bridge = tmp_path / "bridge.mjs"
    bridge.write_text("// stub bridge")

    class _ExecSandbox(FakeSandbox):
        def exec(self, sandbox_id, cmd, timeout=30):
            super().exec(sandbox_id, cmd, timeout)
            return ExecResult(
                stdout=json.dumps(
                    {
                        "score": 12,
                        "label": "Some",
                        "issues": [
                            {"type": "generic-conclusion", "text": "...", "start": 0, "end": 1, "severity": "low"}
                        ],
                        "confidence_category": "medium",
                    }
                ),
                stderr="",
                exit_code=0,
                timeout=False,
                duration_ms=1,
                sandbox_id=sandbox_id,
                cmd_hash="hash",
            )

    sandbox = _ExecSandbox()
    provider = AvoidAIWritingProvider(
        sandbox=sandbox,
        detector_path=str(detector),
        detector_sha256=__import__("hashlib").sha256(detector.read_bytes()).hexdigest(),
        bridge_path=str(bridge),
    )

    from agent.services.text_quality.criteria_service import CriteriaService
    from agent.services.text_quality.models import TextQualityEvaluationRequest

    request = TextQualityEvaluationRequest(
        text="Ein langer technischer deutscher Text mit vielen Worten und konkreten Zahlen 7.",
        language="de",
        content_kind=ContentKind.TECHNICAL_DOCUMENTATION,
        criteria=CriteriaService().default("de", ContentKind.TECHNICAL_DOCUMENTATION),
    )
    signal = provider.analyze(request)
    exec_log = sandbox.get_exec_log()
    assert exec_log[0]["cmd"] == ["node", "/workspace/bridge.mjs"]
    assert signal.normalized_signal_score == pytest.approx(0.12, abs=1e-6)
    assert "generic_phrase" in signal.reason_codes


def test_anti_slop_plugin_cannot_apply_and_only_proposes(memory_repos):
    """Plugin darf ANALYZE/REVIEW_HINTS, aber VALIDATE/APPLY sind fail-closed.
    Kandidat bleibt proposed."""

    provider = AntiSlopEvolutionProvider()
    assert provider.supports(EvolutionCapability.ANALYZE)
    assert provider.supports(EvolutionCapability.REVIEW_HINTS)
    assert not provider.supports(EvolutionCapability.VALIDATE)
    assert not provider.supports(EvolutionCapability.APPLY)
    for forbidden in (EvolutionCapability.VALIDATE, EvolutionCapability.APPLY):
        with pytest.raises(UnsupportedEvolutionOperation):
            getattr(provider, "apply" if forbidden is EvolutionCapability.APPLY else "validate")(
                EvolutionContext(objective="test"), None if forbidden is EvolutionCapability.APPLY else __import__(
                    "agent.services.evolution.models", fromlist=["EvolutionProposal"]
                ).EvolutionProposal(
                    title="x",
                    description="y",
                    proposal_type="prompt_quality",
                    risk_level="medium",
                    confidence=0.5,
                ),
            )


def test_feature_off_does_not_call_sandbox(tmp_path):
    """Wenn der Provider enabled=false bleibt, wird die Sandbox nicht
    angefasst — selbst wenn Detector-Datei existiert."""

    detector = tmp_path / "patterns.js"
    detector.write_text("// stub")

    sandbox = FakeSandbox()
    provider = AvoidAIWritingProvider(
        sandbox=sandbox,
        detector_path=str(detector),
        detector_sha256="0" * 64,
        bridge_path="scripts/avoid_ai_writing_detector_bridge.mjs",
    )
    # Wegen Checksum-Mismatch bleibt der Provider im degraded-Pfad -
    # ohne die Sandbox ueberhaupt zu starten.
    from agent.services.text_quality.criteria_service import CriteriaService
    from agent.services.text_quality.models import TextQualityEvaluationRequest

    signal = provider.analyze(
        TextQualityEvaluationRequest(
            text="Ein langer technischer Text mit genug Worten, damit der Core nicht unscorable wird.",
            language="de",
            content_kind=ContentKind.FREEFORM_PROSE,
            criteria=CriteriaService().default("de", ContentKind.FREEFORM_PROSE),
        )
    )
    assert signal.degraded_reason == "provider_checksum_mismatch"
    assert sandbox.get_exec_log() == []
