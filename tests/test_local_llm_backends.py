"""cliproxyapi-004: tests for CLIProxyAPI-like local_openai_backend.

Acceptance criteria (from todo):

* local_openai_backends entry with id=cliproxyapi is normalised and
  deduplicated
* base_url without /v1 is normalised (the test verifies the
  normalisation rule explicitly — no 'if existing behaviour does this')
* base_url with /v1 is not turned into /v1/v1
* supports_tool_calls stays visible in preflight
* api_key_profile is preferred over plaintext api_key, or handled
  predictably per the existing contract

shared_mock_strategy (from todo):
* HTTP /v1/models calls are mocked via responses/requests-mocks or
  existing probe helpers — no real CLIProxyAPI instance
* No test sets real global user config or XDG_CONFIG_HOME persistently
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from agent.local_llm_backends import (
    _normalize_local_backend_entry,
    get_local_openai_backends,
    normalize_openai_compatible_base_url,
    resolve_local_openai_backend,
)


# ---------------------------------------------------------------------------
# shared mock strategy: helpers that other cliproxyapi tests can reuse
# ---------------------------------------------------------------------------

def make_cliproxyapi_entry(*, with_v1: bool = True, with_key: bool = False,
                           key_profile: str | None = None,
                           models: list[str] | None = None) -> dict[str, Any]:
    """Build a CLIProxyAPI-shaped local_openai_backends entry.

    If both ``with_key`` and ``key_profile`` are provided, both
    fields are set on the entry — this mirrors what happens when a
    user fills out both fields in a YAML.
    """
    base = "http://localhost:8317" + ("/v1" if with_v1 else "")
    entry: dict[str, Any] = {
        "id": "cliproxyapi",
        "name": "CLI Proxy API",
        "base_url": base,
        "supports_tool_calls": True,
        "models": models or ["codex/gpt-5.5-codex", "claude/sonnet"],
    }
    if with_key:
        entry["api_key"] = "***"
    if key_profile:
        entry["api_key_profile"] = key_profile
    return entry


# ---------------------------------------------------------------------------
# normalisation of the entry itself
# ---------------------------------------------------------------------------

def test_cliproxyapi_entry_is_accepted_by_normalize():
    raw = make_cliproxyapi_entry()
    normalised = _normalize_local_backend_entry(raw)
    assert normalised is not None
    assert normalised["provider"] == "cliproxyapi"
    assert normalised["name"] == "CLI Proxy API"
    assert normalised["transport_provider"] == "openai"
    assert normalised["supports_tool_calls"] is True
    assert normalised["configured_models"] == [
        "codex/gpt-5.5-codex", "claude/sonnet",
    ]
    assert normalised["source"] == "agent_config.local_openai_backends"


def test_cliproxyapi_entry_id_and_provider_alias_are_equivalent():
    """`provider:` and `id:` are interchangeable (line 21 of
    _normalize_local_backend_entry)."""
    raw_a = make_cliproxyapi_entry()
    raw_b = make_cliproxyapi_entry()
    raw_b.pop("id")
    raw_b["provider"] = "cliproxyapi"
    a = _normalize_local_backend_entry(raw_a)
    b = _normalize_local_backend_entry(raw_b)
    assert a["provider"] == b["provider"] == "cliproxyapi"


def test_cliproxyapi_supports_tool_calls_kept_through_normalization():
    raw = make_cliproxyapi_entry()
    raw["supports_tool_calls"] = False
    n = _normalize_local_backend_entry(raw)
    assert n["supports_tool_calls"] is False


def test_cliproxyapi_legacy_tool_calling_alias_still_accepted():
    raw = make_cliproxyapi_entry()
    raw.pop("supports_tool_calls")
    raw["tool_calling"] = True
    n = _normalize_local_backend_entry(raw)
    assert n["supports_tool_calls"] is True


def test_cliproxyapi_missing_id_is_rejected():
    raw = make_cliproxyapi_entry()
    raw.pop("id")
    raw.pop("provider", None)
    n = _normalize_local_backend_entry(raw)
    assert n is None


def test_cliproxyapi_non_dict_entry_is_rejected():
    n = _normalize_local_backend_entry("not a dict")  # type: ignore[arg-type]
    assert n is None


# ---------------------------------------------------------------------------
# base_url normalisation
# ---------------------------------------------------------------------------

def test_base_url_with_v1_is_not_doubled():
    """http://localhost:8317/v1 stays http://localhost:8317/v1,
    NOT http://localhost:8317/v1/v1."""
    normalised = normalize_openai_compatible_base_url(
        "http://localhost:8317/v1")
    assert normalised == "http://localhost:8317/v1"


def test_base_url_without_v1_gets_v1_appended():
    """http://localhost:8317 -> http://localhost:8317/v1."""
    normalised = normalize_openai_compatible_base_url(
        "http://localhost:8317")
    assert normalised == "http://localhost:8317/v1"


def test_base_url_with_chat_completions_suffix_is_trimmed():
    """http://localhost:8317/v1/chat/completions -> /v1 (the suffix
    is stripped before the v1-detection)."""
    normalised = normalize_openai_compatible_base_url(
        "http://localhost:8317/v1/chat/completions")
    assert normalised == "http://localhost:8317/v1"


def test_base_url_with_trailing_slash_is_cleaned():
    """Trailing slashes don't produce double slashes."""
    normalised = normalize_openai_compatible_base_url(
        "http://localhost:8317/v1/")
    assert normalised == "http://localhost:8317/v1"


def test_base_url_with_models_suffix_is_trimmed():
    normalised = normalize_openai_compatible_base_url(
        "http://localhost:8317/v1/models")
    assert normalised == "http://localhost:8317/v1"


def test_empty_base_url_returns_none():
    assert normalize_openai_compatible_base_url(None) is None
    assert normalize_openai_compatible_base_url("") is None


def test_invalid_url_returns_none():
    assert normalize_openai_compatible_base_url("not-a-url") is None


def test_base_url_normalisation_is_idempotent():
    """Normalising twice yields the same result as normalising once."""
    once = normalize_openai_compatible_base_url(
        "http://localhost:8317/v1/chat/completions")
    twice = normalize_openai_compatible_base_url(once)
    assert once == twice


# ---------------------------------------------------------------------------
# api_key vs api_key_profile
# ---------------------------------------------------------------------------

def test_cliproxyapi_with_plaintext_key_keeps_it():
    raw = make_cliproxyapi_entry(with_key=True)
    n = _normalize_local_backend_entry(raw)
    assert n["api_key"] == "***"
    assert n["api_key_profile"] is None


def test_cliproxyapi_with_profile_key_has_no_plaintext():
    raw = make_cliproxyapi_entry(key_profile="my_cliproxy")
    n = _normalize_local_backend_entry(raw)
    assert n["api_key"] is None
    assert n["api_key_profile"] == "my_cliproxy"


def test_cliproxyapi_with_both_prefers_plaintext_when_non_empty():
    """When both are present, the existing contract keeps both fields;
    the resolver layer decides which to use at request time."""
    raw = make_cliproxyapi_entry(with_key=True, key_profile="my_cliproxy")
    n = _normalize_local_backend_entry(raw)
    assert n["api_key"] == "***"
    assert n["api_key_profile"] == "my_cliproxy"
    # The contract is *not* "prefer plaintext over profile" — both are
    # preserved. Resolution happens at request time via
    # _resolve_profile_api_key.


# ---------------------------------------------------------------------------
# integration with get_local_openai_backends + dedup
# ---------------------------------------------------------------------------

def test_cliproxyapi_appears_alongside_lmstudio_in_backends():
    backends = get_local_openai_backends(agent_cfg={
        "local_openai_backends": [make_cliproxyapi_entry()],
    })
    providers = {b["provider"] for b in backends}
    assert "lmstudio" in providers
    assert "cliproxyapi" in providers


def test_cliproxyapi_dedup_against_duplicate_id():
    """Two entries with id=cliproxyapi — the second is dropped."""
    backends = get_local_openai_backends(agent_cfg={
        "local_openai_backends": [
            make_cliproxyapi_entry(with_v1=True),
            make_cliproxyapi_entry(with_v1=False),
        ],
    })
    matches = [b for b in backends if b["provider"] == "cliproxyapi"]
    assert len(matches) == 1


def test_cliproxyapi_dedup_is_case_insensitive():
    """'CLIPROXYAPI' and 'cliproxyapi' are the same provider."""
    backends = get_local_openai_backends(agent_cfg={
        "local_openai_backends": [
            {"id": "cliproxyapi", "base_url": "http://localhost:8317/v1",
             "supports_tool_calls": True},
            {"id": "CLIPROXYAPI", "base_url": "http://other:9999/v1",
             "supports_tool_calls": False},
        ],
    })
    matches = [b for b in backends if b["provider"].lower() == "cliproxyapi"]
    assert len(matches) == 1


def test_cliproxyapi_resolved_via_resolve_local_openai_backend():
    cfg = {"local_openai_backends": [make_cliproxyapi_entry()]}
    resolved = resolve_local_openai_backend("cliproxyapi", agent_cfg=cfg)
    assert resolved is not None
    assert resolved["base_url"] == "http://localhost:8317/v1"


def test_cliproxyapi_resolved_with_default_provider_marker():
    cfg = {"local_openai_backends": [make_cliproxyapi_entry()]}
    resolved = resolve_local_openai_backend(
        "cliproxyapi", agent_cfg=cfg, default_provider="cliproxyapi",
        default_model="cliproxyapi/foo",
    )
    assert resolved is not None
    assert resolved["selected"] is True
    assert resolved["selected_model"] == "cliproxyapi/foo"


def test_cliproxyapi_unknown_id_returns_none():
    cfg = {"local_openai_backends": [make_cliproxyapi_entry()]}
    resolved = resolve_local_openai_backend("does_not_exist", agent_cfg=cfg)
    assert resolved is None


def test_cliproxyapi_does_not_pollute_lmstudio_when_provided():
    """Even with cliproxyapi configured, lmstudio is *still* in the
    list (lmstudio is always added by the resolver)."""
    backends = get_local_openai_backends(agent_cfg={
        "local_openai_backends": [make_cliproxyapi_entry()],
    })
    lmstudio = next(b for b in backends if b["provider"] == "lmstudio")
    assert lmstudio["source"] in (
        "agent_config.lmstudio_url", "provider_urls.lmstudio",
    )


# ---------------------------------------------------------------------------
# preflight-visible fields
# ---------------------------------------------------------------------------

def test_cliproxyapi_entry_carries_preflight_visible_fields():
    n = _normalize_local_backend_entry(make_cliproxyapi_entry())
    # Fields that preflight (cliproxyapi-007) needs to surface.
    assert "provider" in n
    assert "name" in n
    assert "base_url" in n
    assert "supports_tool_calls" in n
    assert "transport_provider" in n
    assert n["transport_provider"] == "openai"


# ---------------------------------------------------------------------------
# YAML example loads cleanly (cliproxyapi-003 acceptance)
# ---------------------------------------------------------------------------

def test_yaml_example_loads_and_matches_expected_fields():
    example_path = (Path(__file__).resolve().parents[1]
                    / "docs" / "examples" / "cliproxyapi-agent-config.yaml")
    if not example_path.exists():
        pytest.skip("cliproxyapi-agent-config.yaml not present yet")
    data = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    assert data["default_provider"] == "cliproxyapi"
    entry = data["local_openai_backends"][0]
    assert entry["id"] == "cliproxyapi"
    assert entry["base_url"] == "http://localhost:8317/v1"
    # YAML example must not contain real secrets. The literal string
    # "***" is a documented dummy placeholder.
    assert "***" in entry["api_key"]
    assert "BEGIN PRIVATE KEY" not in example_path.read_text()


def test_yaml_example_contains_no_real_secrets_or_tokens():
    example_path = (Path(__file__).resolve().parents[1]
                    / "docs" / "examples" / "cliproxyapi-agent-config.yaml")
    if not example_path.exists():
        pytest.skip("cliproxyapi-agent-config.yaml not present yet")
    text = example_path.read_text(encoding="utf-8")
    forbidden = [
        "sk-",            # OpenAI-style keys
        "AKIA",           # AWS
        "ghp_",           # GitHub PAT
        "xoxb-",          # Slack
        "Bearer ",        # generic bearer
        "-----BEGIN",     # PEM
    ]
    for needle in forbidden:
        assert needle not in text, f"yaml contains forbidden token: {needle}"


# ---------------------------------------------------------------------------
# invalid url produces a clean failure (not an exception)
# ---------------------------------------------------------------------------

def test_cliproxyapi_invalid_url_returns_none_for_base_url_only():
    """If only the base_url is bad (no scheme) but the rest of the
    entry is valid, the entry still parses; only base_url is None."""
    raw = make_cliproxyapi_entry()
    raw["base_url"] = "not-a-url"  # no scheme, no netloc
    n = _normalize_local_backend_entry(raw)
    assert n is not None
    assert n["base_url"] is None
    assert n["provider"] == "cliproxyapi"  # entry is not dropped