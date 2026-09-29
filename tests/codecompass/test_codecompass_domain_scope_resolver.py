"""CCRDS-017: unit tests for the DomainScope model and resolver.

Covers: empty scope, single/multiple domains, strict failure modes,
path normalization, descriptor override, namespace separation and the
write-scope decision contract — all without LlamaIndex/embeddings.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.codecompass.domain_scope import (
    DECISION_ALLOW,
    DECISION_APPROVAL_REQUIRED,
    DECISION_BLOCKED,
    DomainScope,
    DomainScopeViolation,
    HINT_KIND_DOMAIN,
    HINT_KIND_INTERNAL,
    HINT_KIND_NONE,
    HINT_KIND_UNKNOWN,
    ResolvedDomainScope,
    VIOLATION_EMPTY_SCOPE,
    VIOLATION_UNKNOWN_DOMAIN,
    VIOLATION_WRITE_OUT_OF_SCOPE,
    build_approval_requirement,
    decide_cross_domain_write,
    is_path_within,
    normalize_repo_relative_path,
    parse_domain_hint,
    validate_write_path,
)
from agent.codecompass.domain_scope_resolver import (
    DomainScopeResolver,
    scope_from_domain_hint,
)


def _write_detected(tmp_path: Path, domains: list[dict]) -> Path:
    artifact = tmp_path / "artifacts" / "codecompass" / "domains.detected.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "schema": "codecompass_domain_analysis.v1",
                "project_root": str(tmp_path),
                "generated_at": "2026-06-12T00:00:00Z",
                "domains": domains,
            }
        ),
        encoding="utf-8",
    )
    return artifact


def _resolver(tmp_path: Path) -> DomainScopeResolver:
    return DomainScopeResolver(repo_root=tmp_path)


# ---------------------------------------------------------------- hint parsing


def test_parse_domain_hint_kinds() -> None:
    assert parse_domain_hint("") == (HINT_KIND_NONE, "")
    assert parse_domain_hint(None) == (HINT_KIND_NONE, "")
    assert parse_domain_hint("worker") == (HINT_KIND_INTERNAL, "worker")
    assert parse_domain_hint("codecompass") == (HINT_KIND_INTERNAL, "codecompass")
    assert parse_domain_hint("domain:bestellmodul") == (HINT_KIND_DOMAIN, "bestellmodul")
    assert parse_domain_hint("Domain:Bestellmodul") == (HINT_KIND_DOMAIN, "bestellmodul")
    # Capability-domain ids stay unknown without prefix (CCRDS-006).
    assert parse_domain_hint("blender") == (HINT_KIND_UNKNOWN, "blender")
    assert parse_domain_hint("bestellmodul") == (HINT_KIND_UNKNOWN, "bestellmodul")


def test_scope_from_domain_hint_respects_feature_flag() -> None:
    assert scope_from_domain_hint("domain:orders", enabled=False) is None
    assert scope_from_domain_hint("worker", enabled=True) is None
    assert scope_from_domain_hint("blender", enabled=True) is None
    scope = scope_from_domain_hint("domain:orders", enabled=True, strict=True)
    assert scope is not None
    assert scope.selected_domain_ids == ["orders"]
    assert scope.strict is True


# --------------------------------------------------------------- normalization


def test_normalize_repo_relative_path(tmp_path: Path) -> None:
    assert normalize_repo_relative_path("orders/service.py") == "orders/service.py"
    assert normalize_repo_relative_path("orders\\service.py") == "orders/service.py"
    assert normalize_repo_relative_path("./orders/./a.py") == "orders/a.py"
    assert normalize_repo_relative_path("orders/../catalog/x.py") == "catalog/x.py"
    assert normalize_repo_relative_path("../escape.py") is None
    assert normalize_repo_relative_path("~/.ssh/id_rsa") is None
    assert normalize_repo_relative_path("") is None
    inside = tmp_path / "orders" / "service.py"
    assert normalize_repo_relative_path(str(inside), repo_root=tmp_path) == "orders/service.py"
    assert normalize_repo_relative_path("/etc/passwd", repo_root=tmp_path) is None
    assert normalize_repo_relative_path("/etc/passwd") is None


def test_is_path_within_is_segment_based() -> None:
    assert is_path_within("orders/service.py", ["orders"])
    assert is_path_within("orders", ["orders"])
    assert not is_path_within("orders_extra/file.py", ["orders"])
    assert not is_path_within("catalog/x.py", ["orders"])
    assert not is_path_within("", ["orders"])


# ----------------------------------------------------------------- data model


def test_empty_scope_is_valid_and_inactive(tmp_path: Path) -> None:
    scope = DomainScope()
    assert scope.is_empty
    resolved = _resolver(tmp_path).resolve(scope)
    assert resolved.active is False
    assert resolved.ok
    assert _resolver(tmp_path).resolve(None).active is False


def test_resolved_paths_are_normalized_posix(tmp_path: Path) -> None:
    _write_detected(tmp_path, [
        {"domain_id": "orders", "display_name": "Orders", "confidence": 0.9,
         "root_paths": ["orders\\submodule", "./orders"]},
    ])
    resolved = _resolver(tmp_path).resolve(DomainScope(selected_domain_ids=["orders"]))
    assert resolved.allowed_read_paths == ["orders", "orders/submodule"]


# -------------------------------------------------------------------- resolve


def test_resolver_resolves_single_domain_from_detected(tmp_path: Path) -> None:
    _write_detected(tmp_path, [
        {"domain_id": "bestellmodul", "display_name": "Bestellmodul",
         "confidence": 0.83, "root_paths": ["orders", "shared/order_models"]},
        {"domain_id": "katalog", "display_name": "Artikelkatalog",
         "confidence": 0.7, "root_paths": ["catalog"]},
    ])
    resolved = _resolver(tmp_path).resolve(DomainScope(selected_domain_ids=["bestellmodul"]))
    assert resolved.active and resolved.ok
    assert resolved.allowed_read_paths == ["orders", "shared/order_models"]
    assert resolved.allowed_write_paths == ["orders", "shared/order_models"]
    assert resolved.source_domains[0]["domain_id"] == "bestellmodul"
    assert any("detected:" in p for p in resolved.provenance)


def test_resolver_resolves_multiple_domains(tmp_path: Path) -> None:
    _write_detected(tmp_path, [
        {"domain_id": "orders", "display_name": "Orders", "confidence": 0.8, "root_paths": ["orders"]},
        {"domain_id": "billing", "display_name": "Billing", "confidence": 0.8, "root_paths": ["billing"]},
    ])
    resolved = _resolver(tmp_path).resolve(DomainScope(selected_domain_ids=["orders", "billing"]))
    assert resolved.ok
    assert resolved.allowed_read_paths == ["billing", "orders"]
    assert len(resolved.source_domains) == 2


def test_unknown_domain_strict_is_violation(tmp_path: Path) -> None:
    _write_detected(tmp_path, [
        {"domain_id": "orders", "display_name": "Orders", "confidence": 0.8, "root_paths": ["orders"]},
    ])
    resolved = _resolver(tmp_path).resolve(
        DomainScope(selected_domain_ids=["nope"], strict=True)
    )
    assert not resolved.ok
    assert resolved.violations[0].kind == VIOLATION_UNKNOWN_DOMAIN


def test_unknown_domain_non_strict_warns_without_invented_paths(tmp_path: Path) -> None:
    _write_detected(tmp_path, [
        {"domain_id": "orders", "display_name": "Orders", "confidence": 0.8, "root_paths": ["orders"]},
    ])
    resolved = _resolver(tmp_path).resolve(
        DomainScope(selected_domain_ids=["nope"], strict=False)
    )
    assert resolved.ok
    assert resolved.allowed_read_paths == []
    assert any(w.startswith("unknown_domain_ignored:nope") for w in resolved.warnings)


def test_missing_artifact_strict_fails_closed(tmp_path: Path) -> None:
    resolved = _resolver(tmp_path).resolve(
        DomainScope(selected_domain_ids=["orders"], strict=True)
    )
    assert not resolved.ok
    assert any(w.startswith("domain_artifact_missing") for w in resolved.warnings)


def test_broken_artifact_json_is_error_not_exception(tmp_path: Path) -> None:
    artifact = tmp_path / "artifacts" / "codecompass" / "domains.detected.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("{not json", encoding="utf-8")
    domains, errors = _resolver(tmp_path).load_detected_domains()
    assert domains == []
    assert any(e.startswith("domain_artifact_unreadable") for e in errors)


def test_wrong_schema_is_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "artifacts" / "codecompass" / "domains.detected.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({"schema": "other.v9", "domains": []}), encoding="utf-8")
    domains, errors = _resolver(tmp_path).load_detected_domains()
    assert domains == []
    assert any("schema_mismatch" in e for e in errors)


def test_duplicate_domain_ids_reported(tmp_path: Path) -> None:
    _write_detected(tmp_path, [
        {"domain_id": "orders", "display_name": "A", "confidence": 0.5, "root_paths": ["a"]},
        {"domain_id": "orders", "display_name": "B", "confidence": 0.5, "root_paths": ["b"]},
    ])
    _, errors = _resolver(tmp_path).load_detected_domains()
    assert any(e.startswith("domain_artifact_duplicate_id:orders") for e in errors)


# ----------------------------------------------------------------- descriptors


def _write_descriptor(tmp_path: Path, domain_id: str, payload: dict) -> None:
    target = tmp_path / "domains" / domain_id / "domain.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def test_descriptor_source_paths_win_over_detected_with_warning(tmp_path: Path) -> None:
    _write_detected(tmp_path, [
        {"domain_id": "orders", "display_name": "Orders", "confidence": 0.8,
         "root_paths": ["orders", "legacy_orders"]},
    ])
    _write_descriptor(tmp_path, "orders", {
        "schema": "domain_descriptor.v1",
        "domain_id": "orders",
        "source_paths": {"code_paths": ["orders"], "docs_paths": []},
        "rag_profiles": [{"allowed_paths": ["docs/orders"]}],
    })
    resolved = _resolver(tmp_path).resolve(DomainScope(selected_domain_ids=["orders"]))
    assert resolved.ok
    assert resolved.allowed_read_paths == ["docs/orders", "orders"]
    assert any(w.startswith("descriptor_overrides_detected:orders") for w in resolved.warnings)
    assert any(p.startswith("orders<-descriptor:") for p in resolved.provenance)


def test_capability_descriptor_without_code_paths_is_ignored(tmp_path: Path) -> None:
    # domains/<id>/ today holds capability descriptors (blender, ...) that
    # carry no code paths — they must never become a path scope.
    _write_detected(tmp_path, [
        {"domain_id": "orders", "display_name": "Orders", "confidence": 0.8, "root_paths": ["orders"]},
    ])
    _write_descriptor(tmp_path, "orders", {
        "schema": "domain_descriptor.v1",
        "domain_id": "orders",
        "source_paths": {"code_paths": [], "docs_paths": ["docs/x.md"]},
        "rag_profiles": [],
    })
    resolved = _resolver(tmp_path).resolve(DomainScope(selected_domain_ids=["orders"]))
    # docs_paths are not code paths; descriptor yields nothing → detected wins.
    assert resolved.allowed_read_paths == ["orders"]


def test_descriptor_paths_with_traversal_are_rejected(tmp_path: Path) -> None:
    _write_detected(tmp_path, [
        {"domain_id": "orders", "display_name": "Orders", "confidence": 0.8, "root_paths": ["orders"]},
    ])
    _write_descriptor(tmp_path, "orders", {
        "schema": "domain_descriptor.v1",
        "domain_id": "orders",
        "source_paths": {"code_paths": ["../outside", "orders"]},
    })
    resolved = _resolver(tmp_path).resolve(DomainScope(selected_domain_ids=["orders"]))
    assert resolved.allowed_read_paths == ["orders"]
    assert any(w.startswith("descriptor_path_rejected:orders") for w in resolved.warnings)


# ------------------------------------------------------------------ list_domains


def test_list_domains_stable_sorted_without_host_paths(tmp_path: Path) -> None:
    _write_detected(tmp_path, [
        {"domain_id": "zeta", "display_name": "Z", "confidence": 0.6, "root_paths": ["z"]},
        {"domain_id": "alpha", "display_name": "A", "confidence": 0.9, "root_paths": ["a"]},
    ])
    listing = _resolver(tmp_path).list_domains()
    assert [d["domain_id"] for d in listing["domains"]] == ["alpha", "zeta"]
    assert not Path(listing["artifact_path"]).is_absolute()


# ----------------------------------------------------------------- write scope


def _active_scope() -> ResolvedDomainScope:
    return ResolvedDomainScope(
        active=True,
        strict=True,
        selected_domain_ids=["orders"],
        allowed_read_paths=["orders"],
        allowed_write_paths=["orders"],
    )


def test_validate_write_path_allows_inside_scope() -> None:
    decision = validate_write_path(_active_scope(), "orders/service.py")
    assert decision.decision == DECISION_ALLOW


def test_validate_write_path_blocks_outside_scope() -> None:
    decision = validate_write_path(_active_scope(), "catalog/service.py")
    assert decision.decision == DECISION_BLOCKED
    assert decision.violation is not None
    assert decision.violation.kind == VIOLATION_WRITE_OUT_OF_SCOPE
    assert decision.violation.requested_path == "catalog/service.py"
    assert decision.violation.allowed_paths == ("orders",)
    assert decision.violation.severity == "critical"


def test_validate_write_path_blocks_traversal() -> None:
    decision = validate_write_path(_active_scope(), "orders/../../etc/passwd")
    assert decision.decision == DECISION_BLOCKED


def test_validate_write_path_inactive_scope_allows() -> None:
    decision = validate_write_path(ResolvedDomainScope(active=False), "anything/x.py")
    assert decision.decision == DECISION_ALLOW


def test_decide_cross_domain_write_modes() -> None:
    violation = DomainScopeViolation(kind=VIOLATION_WRITE_OUT_OF_SCOPE, message="x", requested_path="catalog/a.py")
    assert decide_cross_domain_write(violation, mode="strict").decision == DECISION_BLOCKED
    approval = decide_cross_domain_write(violation, mode="approval")
    assert approval.decision == DECISION_APPROVAL_REQUIRED
    assert approval.violation is violation


def test_build_approval_requirement_binds_path_and_arguments() -> None:
    violation = DomainScopeViolation(kind=VIOLATION_WRITE_OUT_OF_SCOPE, message="x", requested_path="catalog/a.py")
    first = build_approval_requirement(violation, arguments={"content": "abc"})
    same = build_approval_requirement(violation, arguments={"content": "abc"})
    other = build_approval_requirement(violation, arguments={"content": "DIFFERENT"})
    assert first["arguments_digest"] == same["arguments_digest"]
    assert first["arguments_digest"] != other["arguments_digest"]
    assert first["requested_path"] == "catalog/a.py"
    assert first["approval_class"] == "cross_domain_write"


def test_strict_scope_with_zero_paths_is_violation(tmp_path: Path) -> None:
    _write_detected(tmp_path, [
        {"domain_id": "orders", "display_name": "Orders", "confidence": 0.8, "root_paths": []},
    ])
    resolved = _resolver(tmp_path).resolve(DomainScope(selected_domain_ids=["orders"], strict=True))
    assert not resolved.ok
    assert any(v.kind == VIOLATION_EMPTY_SCOPE for v in resolved.violations)


# -------------------------------------------------- workspace mutation policy hook


def test_mutation_policy_blocks_outside_domain_write_scope(tmp_path: Path) -> None:
    from agent.services.ananta_workspace_mutation_policy import (
        get_ananta_workspace_mutation_policy_service,
    )

    (tmp_path / "orders").mkdir()
    (tmp_path / "catalog").mkdir()
    (tmp_path / "orders" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "catalog" / "bad.py").write_text("y = 2\n", encoding="utf-8")
    manifest = [
        {"workspace_path": "orders/ok.py", "allowed_operations": ["write"]},
        {"workspace_path": "catalog/bad.py", "allowed_operations": ["write"]},
    ]
    result = get_ananta_workspace_mutation_policy_service().evaluate_changed_files(
        workspace_dir=tmp_path,
        changed_rel_paths=["orders/ok.py", "catalog/bad.py"],
        materialization_manifest=manifest,
        domain_allowed_write_paths=["orders"],
    )
    assert result.status == "policy_violation"
    assert result.allowed_changes == ["orders/ok.py"]
    assert {"path": "catalog/bad.py", "reason": "outside_domain_write_scope"} in result.blocked_changes


def test_mutation_policy_without_domain_scope_unchanged(tmp_path: Path) -> None:
    from agent.services.ananta_workspace_mutation_policy import (
        get_ananta_workspace_mutation_policy_service,
    )

    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog" / "ok.py").write_text("y = 2\n", encoding="utf-8")
    manifest = [{"workspace_path": "catalog/ok.py", "allowed_operations": ["write"]}]
    result = get_ananta_workspace_mutation_policy_service().evaluate_changed_files(
        workspace_dir=tmp_path,
        changed_rel_paths=["catalog/ok.py"],
        materialization_manifest=manifest,
    )
    assert result.status == "ok"


# ------------------------------------------------ retrieval profile compatibility


def test_retrieval_profile_internal_hint_still_works() -> None:
    from agent.services.retrieval_profile_service import resolve_profile

    profile = resolve_profile("wie funktioniert der worker?", {}, domain_hint="worker")
    assert profile.domain == "worker"
    assert any(r == "domain_hint:worker" for r in profile.reasons)


def test_retrieval_profile_domain_prefix_hint_not_unknown() -> None:
    from agent.services.retrieval_profile_service import resolve_profile

    profile = resolve_profile("rechnung im bestellmodul", {}, domain_hint="domain:bestellmodul")
    assert any(r == "domain_hint_runtime_scope:bestellmodul" for r in profile.reasons)
    assert not any(r.startswith("domain_hint_unknown") for r in profile.reasons)


def test_retrieval_profile_unknown_unprefixed_hint_ignored() -> None:
    from agent.services.retrieval_profile_service import resolve_profile

    profile = resolve_profile("irgendwas", {}, domain_hint="blender")
    assert any(r.startswith("domain_hint_unknown:blender") for r in profile.reasons)
