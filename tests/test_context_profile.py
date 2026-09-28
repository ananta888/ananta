"""Central context window profile and budget policy (32k / 64k / 128k)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import context_profile as cp
from agent.context_profile import (
    PROFILES,
    ContextBudgets,
    ProviderLimitProbe,
    configured_window,
    context_budgets,
    effective_window,
    normalize_context_window_config,
)

WINDOWS = [32768, 65536, 131072]
PROFILE_OF = {32768: "standard_32k", 65536: "full_64k", 131072: "extended_128k"}


class _Settings(SimpleNamespace):
    """A settings stand-in: ``model_fields_set`` says which values were set explicitly."""


def _settings(profile="standard_32k", tokens=32768, explicit_tokens=False):
    return _Settings(context_profile=profile, default_context_tokens=tokens,
                     model_fields_set={"default_context_tokens"} if explicit_tokens else set())


@pytest.fixture
def profile(monkeypatch):
    """Configure a window through ANANTA_CONTEXT_PROFILE and a provider that reports ``provider_limit``."""
    from agent.config import settings

    def apply(window, provider_limit=None):
        # only the profile: assigning default_context_tokens would mark it "explicitly set" for later tests
        monkeypatch.setattr(settings, "context_profile", PROFILE_OF[window])
        monkeypatch.setattr(settings, "default_provider", "llamacpp")
        probe = ProviderLimitProbe(http_json=lambda *args: {"default_generation_settings": {"n_ctx": provider_limit}})
        cp.set_provider_limit_probe(probe)
        monkeypatch.setattr(cp, "_provider_base_url", lambda provider, cfg: "http://llm:8080/v1")
        return {"llm_config": {"provider": "llamacpp"}}

    yield apply
    cp.set_provider_limit_probe(ProviderLimitProbe())


# --- 1. configured and effective window -------------------------------------------------------------------


@pytest.mark.parametrize("window", WINDOWS)
def test_profile_sets_the_window(window):
    configured = configured_window({}, settings=_settings(PROFILE_OF[window]))
    assert (configured.tokens, configured.profile, configured.source) == (window, PROFILE_OF[window], "env_profile")


def test_explicit_tokens_win_and_runtime_config_wins_over_environment():
    env = _settings("full_64k", tokens=49152, explicit_tokens=True)
    assert configured_window({}, settings=env).tokens == 49152 and configured_window({}, settings=env).profile == "custom"
    runtime = {"context_window": {"profile": "extended_128k"}}
    assert configured_window(runtime, settings=env).source == "runtime_profile"
    assert configured_window({"context_window": {"profile": "custom", "tokens": 40000}}, settings=env).tokens == 40000


def test_existing_installations_with_ananta_context_tokens_stay_at_32k():
    legacy = _settings("standard_32k", tokens=32768, explicit_tokens=True)
    window = configured_window({}, settings=legacy)
    assert (window.tokens, window.profile, window.source) == (32768, "standard_32k", "env_tokens")


# --- 2./3. provider limits --------------------------------------------------------------------------------


@pytest.mark.parametrize("window", WINDOWS)
def test_a_smaller_provider_limit_caps_the_window(profile, window):
    cfg = profile(window, provider_limit=32768)
    effective = effective_window(agent_cfg=cfg)
    assert effective.tokens == 32768
    assert effective.limited_by == ("configured" if window == 32768 else "provider")


@pytest.mark.parametrize("window", WINDOWS)
def test_a_larger_provider_limit_never_widens_the_window(profile, window):
    cfg = profile(window, provider_limit=262144)
    effective = effective_window(agent_cfg=cfg)
    assert effective.tokens == window and effective.limited_by == "configured"
    assert effective.limits["provider"] == 262144


def test_a_seeded_llm_config_limit_is_not_a_model_limit_but_a_real_one_is(profile):
    cfg = profile(131072)
    assert effective_window(agent_cfg={**cfg, "llm_config": {"provider": "llamacpp", "context_limit": 32768}}).tokens \
        == 131072  # the old seed (= 32k default) does not cap a 128k profile
    declared = effective_window(agent_cfg={"llm_config": {"provider": "llamacpp", "context_limit": 16384}})
    assert declared.tokens == 16384 and declared.limited_by == "model"


def test_the_probe_is_cached_and_never_raises():
    calls = []

    def failing(*args):
        calls.append(args)
        raise OSError("down")

    probe = ProviderLimitProbe(http_json=failing, clock=lambda: 0.0)
    assert probe.limit("llamacpp", "http://llm:8080/v1") is None
    assert probe.limit("llamacpp", "http://llm:8080/v1") is None
    assert len(calls) == 1  # cached
    assert probe.limit("openai", "https://api.example") is None  # cloud: nothing to ask


# --- 4.-9. budgets ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("window", WINDOWS)
def test_budgets_never_exceed_the_window(window):
    budgets = ContextBudgets(window)
    budgets.validate()
    for name in cp.SHARES:
        total = budgets.output_reserve + budgets.safety_margin + budgets.fixed_overhead + budgets.tokens(name)
        assert total <= window, name


def test_32k_budgets_are_the_former_fixed_values():
    budgets = ContextBudgets(32768)
    assert [budgets.bundle_tokens(mode) for mode in ("compact", "standard", "full")] == [4096, 12288, 16384]
    assert budgets.chars("tool_result") == 8000
    assert budgets.chars("diff") == 12000 and budgets.chars("planning_segment") == 8000
    assert budgets.chars("compactor_output") == 12000 and budgets.tokens("rag_context") == 3000
    assert budgets.chars("recovery_context") == 32768


@pytest.mark.parametrize("window,bundle_range", [(32768, (12000, 16500)), (65536, (24000, 33000)),
                                                 (131072, (48000, 66000))])
def test_context_bundle_budget_grows_with_the_profile(profile, window, bundle_range):
    from agent.services.context_bundle_service import resolve_context_bundle_policy

    profile(window)
    standard = resolve_context_bundle_policy({"mode": "standard"})
    full = resolve_context_bundle_policy({"mode": "full"})
    assert bundle_range[0] <= standard["total_budget_tokens"] <= full["total_budget_tokens"] <= bundle_range[1]
    assert standard["window_tokens"] == window and standard["window_profile"] == PROFILE_OF[window]
    # an explicit override stays possible but never takes the whole window
    huge = resolve_context_bundle_policy({"mode": "standard", "standard_budget_tokens": 10 * window})
    assert huge["total_budget_tokens"] == ContextBudgets(window).available


@pytest.mark.parametrize("window", WINDOWS)
def test_tool_loop_budget_follows_the_window(profile, window):
    from agent.cli_backends.context_budget import available_chars
    from agent.cli_backends.tool_loop import _tool_result_limits

    profile(window)
    limits = _tool_result_limits({"max_tool_result_chars": 8000})  # persisted old default: derived
    assert limits["max_total_tool_result_chars"] == ContextBudgets(window).chars("tool_results_total")
    assert limits["max_tool_result_chars"] == ContextBudgets(window).chars("tool_result")
    assert _tool_result_limits({"max_tool_result_chars": 3000})["max_tool_result_chars"] == 3000  # explicit
    budgets = ContextBudgets(window)
    fixed = "x" * 4000
    assert available_chars(fixed) == (window - budgets.output_reserve - budgets.safety_margin - 1000) * 4


@pytest.mark.parametrize("window", WINDOWS)
def test_planning_and_recovery_budgets_follow_the_window(profile, window):
    from agent.services.planning_strategies import LLMPlanningStrategy
    from agent.services.propose_policy import ProposePolicy
    from agent.services.task_recovery_planning_service import _recovery_context_chars

    profile(window)
    scale = window // 32768
    assert LLMPlanningStrategy.segment_chars({"segment_context_chars": 8000}) == 8000 * scale
    assert LLMPlanningStrategy.segment_chars({"segment_context_chars": 1400}) == 1400  # hardware profile stays
    assert _recovery_context_chars() == window  # a quarter of the window, in chars
    assert ProposePolicy(context_compactor_max_output_chars=12000).context_compactor_max_output_chars == 12000 * scale


@pytest.mark.parametrize("window", WINDOWS)
def test_codecompass_and_rlm_budgets_follow_the_window(profile, window):
    from agent.services.codecompass_context_planner_service import CodeCompassContextBudget
    from agent.services.codecompass_rlm_service import rlm_token_budgets

    profile(window)
    budgets = ContextBudgets(window)
    assert CodeCompassContextBudget.from_raw({}).max_tokens == budgets.tokens("evidence")
    assert CodeCompassContextBudget.from_raw({"max_tokens": 150_000}).max_tokens == budgets.available  # was 200k
    rlm = rlm_token_budgets(4)
    assert rlm["synthesis_tokens"] == budgets.tokens("evidence") and rlm["child_tokens"] == rlm["synthesis_tokens"] // 4


def test_a_32k_model_under_a_128k_profile_gets_32k_budgets(profile):
    from agent.services.context_bundle_service import resolve_context_bundle_policy

    profile(131072, provider_limit=32768)
    assert context_budgets().window == 32768
    assert resolve_context_bundle_policy({"mode": "full"})["total_budget_tokens"] == 16384


# --- 7. long-context strategy -----------------------------------------------------------------------------


@pytest.mark.parametrize("window,expected", [(32768, "sequential"), (65536, "compact"), (131072, None)])
def test_the_same_material_is_split_at_32k_and_fits_at_128k(profile, window, expected):
    from agent.services.context_strategy_service import ContextStrategyRequest, ContextStrategyService
    from agent.services.long_context_coordinator import material_fit

    profile(window)
    material = "Zeile mit Inhalt. " * 11_200  # ~50k tokens of ordered material
    service = ContextStrategyService(config={"mode": "active", "ask_decision_provider": False})
    fit = material_fit(material, service)
    if expected is None:
        assert fit.fits  # 128k: no splitting at all
        return
    decision = service.decide(ContextStrategyRequest(fit=fit, input_kind="ordered"))
    assert decision.strategy == expected  # 32k: ~3x the room -> sequential; 64k: just above -> compact
    assert fit.budget_tokens == ContextBudgets(window).available
    if expected == "sequential":
        assert decision.parameters["chunk_budget_tokens"] == int(ContextBudgets(window).available * 0.6)


# --- 10. no silent truncation -----------------------------------------------------------------------------


def test_budget_enforcement_is_recorded():
    from agent.cli_backends.context_budget import fit_blocks
    from agent.context_window import truncation_scope

    with truncation_scope() as events:
        kept = fit_blocks(["a" * 5000, "b" * 5000, "c" * 5000], 5500, site="tool_loop.results")
    # newest complete, older condensed or left out -- and every shortening is recorded
    assert kept[-1] == "c" * 5000 and sum(len(block) for block in kept) <= 5500
    assert events and events[0].site == "tool_loop.results"


# --- config updates ---------------------------------------------------------------------------------------


def test_context_window_updates_are_validated():
    assert normalize_context_window_config({"profile": "64k"}) == {"profile": "full_64k", "tokens": None}
    assert normalize_context_window_config({"tokens": 40000}) == {"profile": "custom", "tokens": 40000}
    assert normalize_context_window_config(None) == {}
    for bad in ({"profile": "huge"}, {"profile": "custom"}, {"tokens": 100}, "x"):
        with pytest.raises(ValueError):
            normalize_context_window_config(bad)
    assert set(PROFILES) == {"compact_12k", "standard_32k", "full_64k", "extended_128k"}


def test_config_api_switches_the_profile(client, admin_auth_header):
    response = client.post("/config", json={"context_window": {"profile": "full_64k"}}, headers=admin_auth_header)
    assert response.status_code == 200
    summary = client.get("/config/context-window", headers=admin_auth_header).get_json()["data"]
    assert summary["configured"] == {"profile": "full_64k", "tokens": 65536, "source": "runtime_profile"}
    assert summary["budgets"]["window_tokens"] <= 65536
    bad = client.post("/config", json={"context_window": {"profile": "huge"}}, headers=admin_auth_header)
    assert bad.status_code == 400
    client.post("/config", json={"context_window": {}}, headers=admin_auth_header)


# --- hub -> worker assignment -----------------------------------------------------------------------------


def test_the_hub_assigned_window_wins_on_the_worker_and_is_scoped():
    from agent.context_profile import assigned_window_scope, hub_assignment

    worker_env = _settings("standard_32k")
    assert hub_assignment({"context_window": {"profile": "full_64k"}}) == {"profile": "full_64k", "tokens": None}
    with assigned_window_scope({"profile": "full_64k"}):
        window = configured_window({}, settings=worker_env)
        assert (window.tokens, window.source) == (65536, "hub_assignment")
    assert configured_window({}, settings=worker_env).tokens == 32768  # only for that task
    with assigned_window_scope({"profile": "nonsense"}):  # invalid: ignored, never widened
        assert configured_window({}, settings=worker_env).tokens == 32768


def test_the_worker_route_applies_the_assigned_window(client, admin_auth_header, monkeypatch):
    from agent.context_profile import configured_window as current_window

    seen = []

    class _Service:
        def propose_task_step(self, tid, data, **kwargs):
            seen.append(current_window())
            from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

            return TaskScopedRouteResponse(data={"reason": "ok", "raw": ""})

    monkeypatch.setattr("agent.routes.tasks.execution._services",
                        lambda: SimpleNamespace(task_scoped_execution_service=_Service()))
    response = client.post("/tasks/T-1/step/propose", json={"task_id": "T-1", "context_window": {"profile": "full_64k"}},
                           headers=admin_auth_header)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert seen and seen[0].tokens == 65536 and seen[0].source == "hub_assignment"


# --- local runtimes vs. subscription / cloud ------------------------------------------------------------


@pytest.mark.parametrize("window", WINDOWS)
def test_the_profile_limits_local_runtimes_only(profile, window):
    from agent.context_profile import is_local_provider, window_for_provider

    profile(window)
    assert window_for_provider("llamacpp") == window and window_for_provider("ollama") == window
    assert window_for_provider("anthropic") is None and window_for_provider("openai") is None  # own window
    assert window_for_provider("openai", requested=50_000) == 50_000  # an explicit limit still applies
    assert window_for_provider("llamacpp", requested=10 * window) == window  # never above the local window
    assert is_local_provider("my-gpu", {"local_openai_backends": [{"id": "my-gpu", "base_url": "http://x/v1"}]})
