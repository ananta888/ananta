import hashlib
import json

from agent.services.sandbox_backend import ExecResult, FakeSandbox
from agent.services.text_quality.criteria_service import CriteriaService
from agent.services.text_quality.models import (
    ContentKind,
    TextQualityEvaluationRequest,
)
from agent.services.text_quality.providers.avoid_ai_writing_provider import (
    AvoidAIWritingProvider,
)


class ResultSandbox(FakeSandbox):
    def exec(self, sandbox_id, cmd, timeout=30):
        super().exec(sandbox_id, cmd, timeout)
        return ExecResult(
            stdout=json.dumps(
                {
                    "score": 20,
                    "label": "Some",
                    "issues": [],
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


def test_provider_uses_fixed_sandbox_argv(tmp_path):
    detector = tmp_path / "patterns.js"
    detector.write_text("module.exports={analyzeText:()=>({})}")
    bridge = tmp_path / "bridge.mjs"
    bridge.write_text("test")
    sandbox = ResultSandbox()
    provider = AvoidAIWritingProvider(
        sandbox=sandbox,
        detector_path=str(detector),
        detector_sha256=hashlib.sha256(detector.read_bytes()).hexdigest(),
        bridge_path=str(bridge),
    )
    request = TextQualityEvaluationRequest(
        text="A sufficiently long and concrete test sentence with twelve words total.",
        criteria=CriteriaService().default("en", ContentKind.FREEFORM_PROSE),
        language="en",
    )
    signal = provider.analyze(request)
    assert signal.normalized_signal_score == 0.2
    assert sandbox.get_exec_log()[0]["cmd"] == ["node", "/workspace/bridge.mjs"]


def test_checksum_mismatch_degrades(tmp_path):
    detector = tmp_path / "patterns.js"
    detector.write_text("bad")
    bridge = tmp_path / "bridge.mjs"
    bridge.write_text("test")
    provider = AvoidAIWritingProvider(
        sandbox=FakeSandbox(),
        detector_path=str(detector),
        detector_sha256="0" * 64,
        bridge_path=str(bridge),
    )
    request = TextQualityEvaluationRequest(
        text="A sufficiently long sentence for this provider contract test to run.",
        criteria=CriteriaService().default("en", ContentKind.FREEFORM_PROSE),
    )
    assert provider.analyze(request).degraded_reason == "provider_checksum_mismatch"
