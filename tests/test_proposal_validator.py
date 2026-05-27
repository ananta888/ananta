"""Tests for HeuristicProposalValidator — T07.01."""
from __future__ import annotations

import pytest

from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition, HeuristicRegistry
from agent.services.heuristic_runtime.proposal_validator import HeuristicProposal, HeuristicProposalValidator


def _make_registry(*heuristics):
    reg = HeuristicRegistry(base_path="/nonexistent")
    reg._loaded = True
    for h in heuristics:
        reg._all.append(h)
        reg._definitions[h.heuristic_id] = h
    return reg


def _make_heuristic(hid="base-h", domain="tui_snake", status="active"):
    h = HeuristicDefinition(
        heuristic_id=hid, version="1.0.0", domain=domain,
        strategy_kind="follow", description="base", deterministic=True,
        safety_class="bounded", capabilities=(), inputs=(), outputs=(), parameters={},
    )
    h = HeuristicDefinition(
        heuristic_id=hid, version="1.0.0", domain=domain,
        strategy_kind="follow", description="base", deterministic=True,
        safety_class="bounded", capabilities=(), inputs=(), outputs=(), parameters={},
        status=status,
    )
    return h


def _valid_proposal(**kwargs) -> HeuristicProposal:
    defaults = dict(
        proposal_id="prop-1",
        proposed_by="ananta-worker",
        domain="tui_snake",
        strategy_kind="follow",
        description="A valid proposal",
        capabilities=["motion_suggest"],
        requested_ttl_seconds=7.0,
        safety_class="bounded",
        deterministic=True,
        version="1.0.0",
    )
    defaults.update(kwargs)
    return HeuristicProposal(**defaults)


# ── valid proposal passes ─────────────────────────────────────────────────────

def test_valid_proposal_passes():
    reg = _make_registry()
    validator = HeuristicProposalValidator(registry=reg)
    result = validator.validate(_valid_proposal())
    assert result.passed, result.reason_codes


# ── schema validation ─────────────────────────────────────────────────────────

def test_empty_description_blocked():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(description="  "))
    assert not result.passed
    assert any("empty_description" in rc for rc in result.reason_codes)


def test_invalid_domain_blocked():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(domain="unknown_domain"))
    assert not result.passed
    assert any("invalid_domain" in rc for rc in result.reason_codes)


def test_invalid_proposer_blocked():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(proposed_by="opencode"))
    assert not result.passed
    assert any("invalid_proposer" in rc for rc in result.reason_codes)


# ── capability violations ─────────────────────────────────────────────────────

def test_network_access_blocked_for_snake():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(capabilities=["network_access"]))
    assert not result.passed
    assert any("capability_violation:network_access" in rc for rc in result.reason_codes)
    assert "network_access" in result.blocked_capabilities


def test_file_write_blocked_for_snake():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(capabilities=["file_write"]))
    assert not result.passed
    assert "file_write" in result.blocked_capabilities


def test_secret_access_blocked_for_snake():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(capabilities=["secret_access"]))
    assert not result.passed


def test_non_deterministic_snake_blocked():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(deterministic=False))
    assert not result.passed
    assert any("non_deterministic" in rc for rc in result.reason_codes)


def test_chat_proposal_allows_read_scope():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(
        domain="chat_codecompass",
        capabilities=["read_source_refs"],
        requested_ttl_seconds=15.0,
    ))
    assert result.passed, result.reason_codes


def test_chat_elevated_allows_broader_caps():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(
        domain="chat_codecompass",
        safety_class="elevated",
        capabilities=["network_access"],
        requested_ttl_seconds=15.0,
    ))
    assert result.passed, result.reason_codes


# ── TTL policy ────────────────────────────────────────────────────────────────

def test_ttl_below_minimum_blocked():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(requested_ttl_seconds=0.1))
    assert not result.passed
    assert any("ttl_out_of_range" in rc for rc in result.reason_codes)


def test_ttl_above_maximum_blocked():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(requested_ttl_seconds=999.0))
    assert not result.passed
    assert any("ttl_out_of_range" in rc for rc in result.reason_codes)


# ── base_heuristic_ref ────────────────────────────────────────────────────────

def test_valid_base_heuristic_ref_passes():
    h = _make_heuristic("base-h")
    validator = HeuristicProposalValidator(registry=_make_registry(h))
    result = validator.validate(_valid_proposal(base_heuristic_ref="base-h"))
    assert result.passed, result.reason_codes


def test_unknown_base_heuristic_ref_blocked():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal(base_heuristic_ref="nonexistent-h"))
    assert not result.passed
    assert any("base_heuristic_not_found" in rc for rc in result.reason_codes)


def test_to_dict():
    validator = HeuristicProposalValidator(registry=_make_registry())
    result = validator.validate(_valid_proposal())
    d = result.to_dict()
    assert "passed" in d
    assert "reason_codes" in d
    assert "blocked_capabilities" in d
