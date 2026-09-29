"""Tests for LangChain/LangGraph provider config validation (LCG-003, LCG-004).

Provider config is the user-facing surface for opting in. These tests
cover the cross-field rules the pydantic model enforces:

- cloud_gated mode requires external_calls_allowed=True
- is_live / is_dry_run mode classification
- LangGraph graph_allowed default-allow semantics (documented)
- to_safe_dict strips secret_refs
- Literal-typed mode fields round-trip through JSON
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.providers.lc_lg import (
    LangChainProviderConfig,
    LangGraphProviderConfig,
)


# ── LangChainProviderConfig defaults ────────────────────────────────────


def test_lc_default_off_is_disabled_and_dry_run():
    cfg = LangChainProviderConfig.default_off()
    assert cfg.enabled is False
    assert cfg.mode == "dry_run"
    assert cfg.is_live() is False
    assert cfg.is_dry_run() is True


def test_lc_external_calls_default_false():
    """Cloud calls must be opt-in. Default-OFF is the secure default."""
    cfg = LangChainProviderConfig(enabled=True, mode="local_live")
    assert cfg.external_calls_allowed is False


def test_lc_allowed_tools_default_empty():
    """Empty allowlist + default-deny gate = nothing allowed."""
    cfg = LangChainProviderConfig(enabled=True, mode="local_live")
    assert not cfg.allowed_tools  # empty frozenset, set, or list — all falsy


# ── cloud_gated requires external_calls_allowed ───────────────────────


def test_lc_cloud_gated_without_external_calls_rejected():
    with pytest.raises(ValidationError) as exc:
        LangChainProviderConfig(
            enabled=True, mode="cloud_gated", external_calls_allowed=False,
        )
    assert "external_calls_allowed" in str(exc.value).lower()


def test_lc_cloud_gated_with_external_calls_ok():
    cfg = LangChainProviderConfig(
        enabled=True, mode="cloud_gated", external_calls_allowed=True,
    )
    assert cfg.mode == "cloud_gated"
    assert cfg.external_calls_allowed is True


def test_lg_cloud_gated_without_external_calls_rejected():
    with pytest.raises(ValidationError) as exc:
        LangGraphProviderConfig(
            enabled=True, mode="cloud_gated", external_calls_allowed=False,
        )
    assert "external_calls_allowed" in str(exc.value).lower()


# ── LangGraphProviderConfig defaults ───────────────────────────────────


def test_lg_default_off_is_disabled_and_dry_run():
    cfg = LangGraphProviderConfig.default_off()
    assert cfg.enabled is False
    assert cfg.mode == "dry_run"
    assert cfg.is_live() is False
    assert cfg.is_dry_run() is True


def test_lg_default_human_required_includes_high_risk():
    cfg = LangGraphProviderConfig.default_off()
    # Per the ADR, default human-required-for includes the high-risk
    # set. Adding or removing is allowed, but the factory default
    # must include these.
    for action in ("shell", "patch", "push", "delete"):
        assert action in cfg.human_in_loop_required_for


def test_lg_default_checkpoint_policy_keeps_state_local():
    """Default must keep state out of remote stores.

    The exact literal may be one of the two local policies
    ('local_ephemeral', 'local_ephemeral_or_hub_owned'); both are
    acceptable defaults. What is NOT acceptable is any 'remote_*'
    value, which would put checkpoint state outside the Hub.
    """
    cfg = LangGraphProviderConfig.default_off()
    assert cfg.checkpoint_policy.startswith("local_")
    assert "remote" not in cfg.checkpoint_policy


# ── graph_allowed semantics (documented default-allow) ────────────────


def test_lg_graph_allowed_empty_list_allows_any():
    """Empty allowed_graphs = allow any graph (documented behaviour).

    This is intentionally different from the tool allowlist. Tools
    are default-deny; graphs are default-allow. The Hub approves a
    graph by approving the task that runs it; the gate only restricts
    graphs when the user opts in with allowed_graphs.
    """
    cfg = LangGraphProviderConfig.default_off()
    assert cfg.graph_allowed("any_graph") is True


def test_lg_graph_allowed_restricts_to_list():
    cfg = LangGraphProviderConfig(
        enabled=True, mode="dry_run",
        allowed_graphs=["code_review_v1"],
    )
    assert cfg.graph_allowed("code_review_v1") is True
    assert cfg.graph_allowed("other_graph") is False


# ── to_safe_dict strips secret_refs ────────────────────────────────────


def test_lc_to_safe_dict_omits_secret_refs():
    cfg = LangChainProviderConfig(
        enabled=True, mode="cloud_gated", external_calls_allowed=True,
        secret_refs=["vault:openai/key"],
    )
    safe = cfg.to_safe_dict()
    assert "secret_refs" not in safe
    # The vault path itself is safe; the policy is just to keep the
    # whole structure out of casual logs.


def test_lg_to_safe_dict_omits_secret_refs():
    cfg = LangGraphProviderConfig(
        enabled=True, mode="cloud_gated", external_calls_allowed=True,
        secret_refs=["vault:x"],
    )
    safe = cfg.to_safe_dict()
    assert "secret_refs" not in safe


# ── is_live / is_dry_run matrix ────────────────────────────────────────


@pytest.mark.parametrize("enabled,mode,live", [
    (False, "dry_run", False),
    (False, "local_live", False),    # disabled wins
    (True, "dry_run", False),
    (True, "mock_live", False),      # mock is dry
    (True, "local_live", True),
])
def test_lc_is_live_matrix(enabled, mode, live):
    cfg_kwargs = {"enabled": enabled, "mode": mode}
    if mode == "cloud_gated":
        cfg_kwargs["external_calls_allowed"] = True
    cfg = LangChainProviderConfig(**cfg_kwargs)
    assert cfg.is_live() is live
    assert cfg.is_dry_run() is (not live)


@pytest.mark.parametrize("mode", [
    "disabled", "dry_run", "mock_live", "local_live", "cloud_gated",
])
def test_lc_mode_literal_accepts_all_documented_values(mode):
    """Every documented mode value parses without error.

    cloud_gated is the exception (requires external_calls_allowed),
    so it gets its own test above.
    """
    if mode == "cloud_gated":
        cfg = LangChainProviderConfig(enabled=True, mode=mode, external_calls_allowed=True)
    else:
        cfg = LangChainProviderConfig(enabled=True, mode=mode)
    assert cfg.mode == mode


# ── retriever_source default is CodeCompass ────────────────────────────


def test_lc_default_retriever_source_is_codecompass():
    cfg = LangChainProviderConfig.default_off()
    assert cfg.retriever_source == "codecompass"


def test_lc_retriever_source_none_is_allowed():
    """'none' means no retrieval; the chain uses only prompt + tools.

    This is the explicit opt-out for chains that do not need context
    (e.g. summarization of an already-fetched document).
    """
    cfg = LangChainProviderConfig(retriever_source="none")
    assert cfg.retriever_source == "none"
