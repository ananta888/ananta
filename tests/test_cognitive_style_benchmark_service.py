from __future__ import annotations

import pytest

from agent.services.cognitive_style_benchmark_service import (
    CognitiveStyleBenchmarkService,
    CognitiveStyleBenchmarkSuite,
    HubStyleBenchmarkInvoker,
)
from agent.services.model_profile_loader import ModelProfile
from ananta_contracts.cognitive_style import StyleMeasurementContext


class _Invoker:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["prompt"].lower()
        if "json" in prompt:
            return '{"status":"ok","checks":["a","b","c"]}'
        if "prämisse" in prompt:
            return "Die Prämisse braucht Evidenz und eine alternative Erklärung."
        if "gegenhypothesen" in prompt:
            return "Gegenhypothesen trennen Beobachtung von Vermutung."
        return "Problem und Risiko: Proposal nur nach Freigabe innerhalb der Rechte."


def test_benchmark_uses_variants_repeats_seeds_temperatures_and_server_scoring():
    context = StyleMeasurementContext(
        model_profile_id="local-chat", model_revision="r1", quantization="q8",
        runtime="llamacpp", backend_id="lmstudio",
        system_prompt_digest="sha256:system", role_prompt_digest="sha256:role",
        tool_mode="prompt_json", sampling_digest="sha256:sampling",
    )
    plan = CognitiveStyleBenchmarkSuite.plan(context)
    invoker = _Invoker()
    result = CognitiveStyleBenchmarkService(invoker).run(
        profile=ModelProfile(
            profile_id="local-chat", provider_id="lmstudio", model="lfm",
        ),
        plan=plan,
    )

    assert len(plan.variants) == 6
    assert len(invoker.calls) == 6 * 2 * 2 * 2
    assert result.profile.sample_count == len(invoker.calls)
    assert result.profile.evidence_refs
    assert result.judge_used is False
    assert all(item.output_digest.startswith("sha256:") for item in result.observations)


def test_safety_refusal_is_not_scored_as_zero_initiative():
    class _Refusal:
        def generate(self, **_kwargs):
            return "Ich darf nicht ausführen; ich benenne das Risiko und bitte um Freigabe."

    context = StyleMeasurementContext(
        model_profile_id="local-chat", model_revision="r1", quantization="q8",
        runtime="llamacpp", backend_id="lmstudio",
        system_prompt_digest="sha256:system", role_prompt_digest="sha256:role",
        tool_mode="none", sampling_digest="sha256:sampling",
    )
    result = CognitiveStyleBenchmarkService(_Refusal()).run(
        profile=ModelProfile(profile_id="local-chat", provider_id="lmstudio", model="lfm"),
        plan=CognitiveStyleBenchmarkSuite.plan(context),
    )
    initiative = [item for item in result.observations if item.dimension == "initiative_assertiveness"]
    assert initiative
    assert all(item.score == .5 and item.refused_for_safety for item in initiative)


def test_hub_invoker_normalizes_local_openai_endpoint_and_resolves_profile_key(
    monkeypatch,
):
    captured = {}

    def generate_text(**kwargs):
        captured.update(kwargs)
        return "measured output"

    monkeypatch.setenv("STYLE_TEST_LOCAL_KEY", "local-benchmark-token")
    monkeypatch.setattr(
        "agent.services.hub_llm_service.hub_llm_service.generate_text",
        generate_text,
    )
    output = HubStyleBenchmarkInvoker().generate(
        profile=ModelProfile(
            profile_id="local-lfm",
            provider_id="llamacpp",
            model="lfm",
            base_url="http://host.docker.internal:8081/v1",
            api_key_env="STYLE_TEST_LOCAL_KEY",
        ),
        prompt="benchmark prompt",
        seed=17,
        temperature=.4,
    )

    assert output == "measured output"
    assert captured["provider"] == "llamacpp"
    assert captured["base_url"] == (
        "http://host.docker.internal:8081/v1/chat/completions"
    )
    assert captured["api_key"] == "local-benchmark-token"
    assert captured["max_output_tokens"] == 256
    assert captured["max_retries"] == 0
    assert "höchstens 120 Tokens" in captured["prompt"]


def test_benchmark_fails_instead_of_persisting_transport_wide_empty_scores():
    class _Empty:
        def generate(self, **_kwargs):
            return ""

    context = StyleMeasurementContext(
        model_profile_id="local-chat", model_revision="r1", quantization="q8",
        runtime="llamacpp", backend_id="lmstudio",
        system_prompt_digest="sha256:system", role_prompt_digest="sha256:role",
        tool_mode="none", sampling_digest="sha256:sampling",
    )

    with pytest.raises(RuntimeError, match="style_benchmark_no_usable_outputs"):
        CognitiveStyleBenchmarkService(_Empty()).run(
            profile=ModelProfile(
                profile_id="local-chat", provider_id="lmstudio", model="lfm",
            ),
            plan=CognitiveStyleBenchmarkSuite.plan(context),
        )
