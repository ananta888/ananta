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
    assert captured["max_output_tokens"] == 1024
    assert captured["max_retries"] == 0
    assert "höchstens 120 Tokens" in captured["prompt"]


def test_hub_invoker_omits_unsupported_per_request_seed(monkeypatch):
    captured = {}

    def generate_text(**kwargs):
        captured.update(kwargs)
        return "measured output"

    monkeypatch.setattr(
        "agent.services.hub_llm_service.hub_llm_service.generate_text",
        generate_text,
    )
    HubStyleBenchmarkInvoker().generate(
        profile=ModelProfile(
            profile_id="local-kat", provider_id="openai_compatible",
            model="kat", base_url="http://runtime:8082/v1",
            supports_seed=False,
        ),
        prompt="benchmark prompt",
        seed=41,
        temperature=0,
    )

    assert captured["seed"] is None


def test_observations_disclose_when_runtime_cannot_apply_seed():
    context = StyleMeasurementContext(
        model_profile_id="local-chat", model_revision="r1", quantization="q8",
        runtime="colibri", backend_id="openai_compatible",
        system_prompt_digest="sha256:system", role_prompt_digest="sha256:role",
        tool_mode="prompt_json", sampling_digest="sha256:sampling",
    )
    result = CognitiveStyleBenchmarkService(_Invoker()).run(
        profile=ModelProfile(
            profile_id="local-chat", provider_id="openai_compatible",
            model="kat", supports_seed=False,
        ),
        plan=CognitiveStyleBenchmarkSuite.plan(context),
    )

    assert all(item.seed_applied is False for item in result.observations)


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

    with pytest.raises(
        RuntimeError, match="style_benchmark_insufficient_output_coverage"
    ):
        CognitiveStyleBenchmarkService(_Empty()).run(
            profile=ModelProfile(
                profile_id="local-chat", provider_id="lmstudio", model="lfm",
            ),
            plan=CognitiveStyleBenchmarkSuite.plan(context),
        )


def test_benchmark_rejects_mostly_empty_final_outputs():
    class _Sparse:
        def __init__(self):
            self.calls = 0

        def generate(self, **_kwargs):
            self.calls += 1
            return '{"status":"ok","checks":["a","b","c"]}' if self.calls <= 8 else ""

    context = StyleMeasurementContext(
        model_profile_id="local-chat", model_revision="r1", quantization="q8",
        runtime="llamacpp", backend_id="lmstudio",
        system_prompt_digest="sha256:system", role_prompt_digest="sha256:role",
        tool_mode="none", sampling_digest="sha256:sampling",
    )

    with pytest.raises(
        RuntimeError, match="style_benchmark_insufficient_output_coverage"
    ):
        CognitiveStyleBenchmarkService(_Sparse()).run(
            profile=ModelProfile(
                profile_id="local-chat", provider_id="lmstudio", model="lfm",
            ),
            plan=CognitiveStyleBenchmarkSuite.plan(context),
        )


def test_server_scorer_accepts_bounded_german_and_english_paraphrases():
    variants = {item.variant_id: item for item in CognitiveStyleBenchmarkSuite.variants()}

    truth_score, truth_refused = CognitiveStyleBenchmarkService._score(
        variants["premise-a"],
        "Diese Annahme braucht einen Nachweis; eine weitere Ursache ist das Netzwerk.",
    )
    initiative_score, initiative_refused = CognitiveStyleBenchmarkService._score(
        variants["initiative-b"],
        "I see an issue and propose a review within my current permissions.",
    )

    assert truth_score == 1
    assert truth_refused is False
    assert initiative_score == 1
    assert initiative_refused is False
