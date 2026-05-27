"""Round 10 — Format system tests.

Covers:
  T05.01 — HeuristicYamlImporter
  T07.01 — HeuristicFormatValidator
  T07.02 — AiProposalGuardrails
  T07.03 — HeuristicActivationGate (already tested implicitly; basic smoke here)
  T07.04 — HeuristicProvenanceTracker
  T08.01 — CLI heuristic commands (smoke tests)
  T08.02 — HeuristicFormatTuiView
  T08.04 — E2E YAML → normalize → validate → candidate flow
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_valid_hdef(**overrides) -> dict:
    base = {
        "heuristic_id": "test_heuristic_one",
        "version": "1.0.0",
        "status": "candidate",
        "domain": "chat_codecompass",
        "description": "A test heuristic for format validation.",
        "deterministic": True,
        "safety_class": "readonly",
        "capabilities": ["read_local_context"],
        "ttl_policy": {"min_seconds": 10.0, "default_seconds": 15.0, "max_seconds": 20.0},
        "runtime": {"mode": "declarative_rules"},
        "inputs": ["query", "context_score"],
        "outputs": ["context_ref"],
        "parameters": {"score_threshold": 0.5},
    }
    base.update(overrides)
    return base


MINIMAL_YAML = """\
heuristic_id: yaml_test_heuristic
version: "1.0.0"
domain: planning
description: A YAML-authored planning heuristic for testing.
deterministic: true
safety_class: readonly
capabilities:
  - read_local_context
  - read_todo_state
ttl_policy:
  min_seconds: 10.0
  default_seconds: 15.0
  max_seconds: 20.0
runtime:
  mode: declarative_rules
inputs:
  - query
outputs:
  - todo_summary
parameters:
  min_score: 0.1
"""

PYTHON_STRATEGY_YAML = """\
heuristic_id: yaml_py_heuristic
version: "1.0.0"
domain: chat_codecompass
description: Python strategy heuristic from YAML.
deterministic: true
safety_class: readonly
capabilities:
  - read_local_context
runtime:
  mode: python_strategy
  python_strategy:
    module: agent.heuristics.strategies.chat_codecompass.symbol_lookup
    class: SymbolLookupStrategy
    expected_inputs: [query]
    expected_outputs: [context_ref]
    required_capabilities: [read_local_context]
inputs:
  - query
outputs:
  - context_ref
parameters:
  min_score: 0.2
"""


# ─────────────────────────────────────────────────────────────────────────────
# T05.01 — HeuristicYamlImporter
# ─────────────────────────────────────────────────────────────────────────────

class TestHeuristicYamlImporter:
    def _importer(self, tmp_path):
        from agent.services.heuristic_runtime.yaml_importer import HeuristicYamlImporter
        return HeuristicYamlImporter(base_path=str(tmp_path))

    def test_import_text_success(self, tmp_path):
        importer = self._importer(tmp_path)
        result = importer.import_text(MINIMAL_YAML)
        assert result.success
        assert result.heuristic_id == "yaml_test_heuristic"
        assert result.content_hash != ""

    def test_import_text_forces_candidate_status(self, tmp_path):
        importer = self._importer(tmp_path)
        yaml_with_active = MINIMAL_YAML + "\nstatus: active\n"
        result = importer.import_text(yaml_with_active)
        assert result.success
        assert "yaml_source_cannot_be_active" in " ".join(result.warnings)

    def test_import_file_writes_to_candidates(self, tmp_path):
        importer = self._importer(tmp_path)
        yaml_file = tmp_path / "yaml_test_heuristic.heuristic.yaml"
        yaml_file.write_text(MINIMAL_YAML)

        result = importer.import_file(str(yaml_file))
        assert result.success
        assert result.candidate_path != ""
        assert os.path.isfile(result.candidate_path)

        with open(result.candidate_path) as f:
            data = json.load(f)
        assert data["heuristic_id"] == "yaml_test_heuristic"
        assert data["status"] == "candidate"

    def test_import_file_dry_run_no_write(self, tmp_path):
        importer = self._importer(tmp_path)
        yaml_file = tmp_path / "yaml_test_heuristic.heuristic.yaml"
        yaml_file.write_text(MINIMAL_YAML)

        result = importer.import_file(str(yaml_file), dry_run=True)
        assert result.success
        assert result.candidate_path == ""  # not written
        candidates_dir = tmp_path / "candidates"
        assert not candidates_dir.exists()

    def test_import_file_not_found(self, tmp_path):
        importer = self._importer(tmp_path)
        result = importer.import_file("/nonexistent/path.yaml")
        assert not result.success
        assert "file_not_found" in result.reason_code

    def test_import_text_invalid_yaml(self, tmp_path):
        importer = self._importer(tmp_path)
        result = importer.import_text("{ invalid yaml: [broken")
        assert not result.success

    def test_import_text_missing_id(self, tmp_path):
        importer = self._importer(tmp_path)
        result = importer.import_text("version: '1.0.0'\ndomain: planning\n")
        assert not result.success
        assert "missing_heuristic_id" in result.reason_code

    def test_import_directory_empty(self, tmp_path):
        importer = self._importer(tmp_path)
        authoring_dir = tmp_path / "authoring"
        authoring_dir.mkdir()
        results = importer.import_directory(str(authoring_dir))
        assert results == []

    def test_import_directory_multiple_files(self, tmp_path):
        importer = self._importer(tmp_path)
        authoring_dir = tmp_path / "authoring"
        authoring_dir.mkdir()
        (authoring_dir / "one.heuristic.yaml").write_text(MINIMAL_YAML)
        (authoring_dir / "two.heuristic.yaml").write_text(
            MINIMAL_YAML.replace("yaml_test_heuristic", "yaml_test_two")
        )
        results = importer.import_directory(str(authoring_dir))
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_import_python_strategy_yaml(self, tmp_path):
        importer = self._importer(tmp_path)
        result = importer.import_text(PYTHON_STRATEGY_YAML)
        assert result.success
        assert result.heuristic_id == "yaml_py_heuristic"


# ─────────────────────────────────────────────────────────────────────────────
# T07.01 — HeuristicFormatValidator
# ─────────────────────────────────────────────────────────────────────────────

class TestHeuristicFormatValidator:
    def _validator(self):
        from agent.services.heuristic_runtime.format_validator import HeuristicFormatValidator
        return HeuristicFormatValidator()

    def test_valid_hdef_passes(self):
        v = self._validator()
        result = v.validate(_make_valid_hdef())
        assert result.passed
        assert result.reason_codes == []

    def test_missing_heuristic_id_fails(self):
        v = self._validator()
        result = v.validate({"version": "1.0.0"})
        assert not result.passed
        assert "missing_heuristic_id" in result.reason_codes

    def test_invalid_version_format(self):
        v = self._validator()
        result = v.validate(_make_valid_hdef(version="1.0"))
        assert not result.passed
        assert any("invalid_version_format" in c for c in result.reason_codes)

    def test_valid_version_3_parts(self):
        v = self._validator()
        result = v.validate(_make_valid_hdef(version="2.3.14"))
        assert result.passed

    def test_invalid_safety_class(self):
        v = self._validator()
        result = v.validate(_make_valid_hdef(safety_class="ultra_secret"))
        assert not result.passed
        assert any("invalid_safety_class" in c for c in result.reason_codes)

    def test_snake_domain_non_deterministic_fails(self):
        v = self._validator()
        result = v.validate(_make_valid_hdef(domain="tui_snake", deterministic=False))
        assert not result.passed
        assert any("snake_domain_must_be_deterministic" in c for c in result.reason_codes)

    def test_snake_domain_deterministic_passes(self):
        v = self._validator()
        result = v.validate(_make_valid_hdef(domain="tui_snake", deterministic=True))
        assert result.passed

    def test_python_strategy_missing_module(self):
        v = self._validator()
        hdef = _make_valid_hdef(runtime={"mode": "python_strategy", "python_strategy": {"class": "Foo"}})
        result = v.validate(hdef)
        assert not result.passed
        assert "python_strategy_missing_module" in result.reason_codes

    def test_python_strategy_missing_class(self):
        v = self._validator()
        hdef = _make_valid_hdef(runtime={"mode": "python_strategy", "python_strategy": {"module": "agent.heuristics.strategies.x.y"}})
        result = v.validate(hdef)
        assert not result.passed
        assert "python_strategy_missing_class" in result.reason_codes

    def test_python_strategy_valid(self):
        v = self._validator()
        hdef = _make_valid_hdef(runtime={
            "mode": "python_strategy",
            "python_strategy": {
                "module": "agent.heuristics.strategies.chat_codecompass.symbol_lookup",
                "class": "SymbolLookupStrategy",
            }
        })
        result = v.validate(hdef)
        assert result.passed

    def test_ttl_invariant_violated(self):
        v = self._validator()
        hdef = _make_valid_hdef(
            ttl_policy={"min_seconds": 20.0, "default_seconds": 10.0, "max_seconds": 30.0}
        )
        result = v.validate(hdef)
        assert not result.passed
        assert any("ttl_policy_invariant_violated" in c for c in result.reason_codes)

    def test_ttl_invariant_ok(self):
        v = self._validator()
        hdef = _make_valid_hdef(
            ttl_policy={"min_seconds": 5.0, "default_seconds": 7.5, "max_seconds": 10.0}
        )
        result = v.validate(hdef)
        assert result.passed

    def test_empty_inputs_item_fails(self):
        v = self._validator()
        hdef = _make_valid_hdef(inputs=["query", ""])
        result = v.validate(hdef)
        assert not result.passed
        assert any("inputs[1]" in c for c in result.reason_codes)

    def test_non_serializable_parameter_fails(self):
        v = self._validator()
        hdef = _make_valid_hdef(parameters={"fn": lambda x: x})
        result = v.validate(hdef)
        assert not result.passed
        assert any("parameter_not_serializable" in c for c in result.reason_codes)

    def test_missing_description_warns(self):
        v = self._validator()
        hdef = _make_valid_hdef(description="")
        result = v.validate(hdef)
        assert result.passed  # only a warning
        assert "missing_description" in result.warnings


# ─────────────────────────────────────────────────────────────────────────────
# T07.02 — AiProposalGuardrails
# ─────────────────────────────────────────────────────────────────────────────

class TestAiProposalGuardrails:
    def _guardrails(self):
        from agent.services.heuristic_runtime.ai_proposal_guardrails import AiProposalGuardrails
        return AiProposalGuardrails()

    def _valid_proposal(self, **overrides) -> dict:
        base = {
            "heuristic_id": "test_ai_heuristic",
            "description": "Detects error patterns in stack traces.",
            "status": "candidate",
            "capabilities": ["read_local_context"],
            "provenance": {"created_by": "ananta-worker"},
            "runtime": {"mode": "declarative_rules"},
        }
        base.update(overrides)
        return base

    def test_valid_proposal_passes(self):
        g = self._guardrails()
        result = g.check(self._valid_proposal())
        assert result.passed
        assert result.rejection_reasons == []

    def test_missing_provenance_created_by(self):
        g = self._guardrails()
        result = g.check(self._valid_proposal(provenance={}))
        assert not result.passed
        assert "missing_provenance_created_by" in result.rejection_reasons

    def test_boilerplate_description_rejected(self):
        g = self._guardrails()
        for bad_desc in ["", "tbd", "placeholder", "TODO"]:
            result = g.check(self._valid_proposal(description=bad_desc))
            assert not result.passed, f"Should reject: {bad_desc!r}"
            assert any("boilerplate_or_missing_description" in r for r in result.rejection_reasons)

    def test_status_active_rejected(self):
        g = self._guardrails()
        result = g.check(self._valid_proposal(status="active"))
        assert not result.passed
        assert any("ai_cannot_set_status_active" in r for r in result.rejection_reasons)

    def test_heuristic_id_not_snake_case_rejected(self):
        g = self._guardrails()
        result = g.check(self._valid_proposal(heuristic_id="My-Heuristic!"))
        assert not result.passed
        assert any("heuristic_id_not_snake_case" in r for r in result.rejection_reasons)

    def test_valid_snake_case_id_passes(self):
        g = self._guardrails()
        result = g.check(self._valid_proposal(heuristic_id="my_valid_heuristic_v2"))
        assert result.passed

    def test_inline_code_anywhere_rejected(self):
        g = self._guardrails()
        proposal = self._valid_proposal()
        proposal["runtime"] = {"mode": "declarative_rules", "inline_code": "import os; os.system('rm -rf /')"}
        result = g.check(proposal)
        assert not result.passed
        assert "inline_code_field_forbidden" in result.rejection_reasons

    def test_inline_code_nested_rejected(self):
        g = self._guardrails()
        proposal = self._valid_proposal()
        proposal["parameters"] = {"config": {"inline_code": "exec('evil')"}}
        result = g.check(proposal)
        assert not result.passed
        assert "inline_code_field_forbidden" in result.rejection_reasons

    def test_python_strategy_wrong_module_prefix_rejected(self):
        g = self._guardrails()
        proposal = self._valid_proposal(runtime={
            "mode": "python_strategy",
            "python_strategy": {"module": "evil.module.path", "class": "EvilClass"},
        })
        result = g.check(proposal)
        assert not result.passed
        assert any("python_strategy_module_not_in_allowed_prefix" in r for r in result.rejection_reasons)

    def test_python_strategy_valid_module_prefix_passes(self):
        g = self._guardrails()
        proposal = self._valid_proposal(runtime={
            "mode": "python_strategy",
            "python_strategy": {
                "module": "agent.heuristics.strategies.chat_codecompass.symbol_lookup",
                "class": "SymbolLookupStrategy",
            },
        })
        result = g.check(proposal)
        assert result.passed

    def test_unknown_capability_warns(self):
        g = self._guardrails()
        result = g.check(self._valid_proposal(capabilities=["read_local_context", "unknown_custom_cap"]))
        assert result.passed  # warning only
        assert any("unknown_capability" in w for w in result.warnings)

    def test_suspicious_capability_rejected(self):
        g = self._guardrails()
        result = g.check(self._valid_proposal(capabilities=["exec evil"]))
        assert not result.passed
        assert any("suspicious_capability_name" in r for r in result.rejection_reasons)


# ─────────────────────────────────────────────────────────────────────────────
# T07.04 — HeuristicProvenanceTracker
# ─────────────────────────────────────────────────────────────────────────────

class TestHeuristicProvenanceTracker:
    def _tracker(self):
        from agent.services.heuristic_runtime.provenance_tracker import HeuristicProvenanceTracker
        return HeuristicProvenanceTracker()

    def test_enrich_adds_content_hash(self):
        t = self._tracker()
        hdef = _make_valid_hdef()
        enriched = t.enrich(hdef, created_by="bootstrap", source_format="json")
        assert "content_hash" in enriched
        assert len(enriched["content_hash"]) == 64  # SHA-256 hex

    def test_enrich_adds_provenance_fields(self):
        t = self._tracker()
        hdef = _make_valid_hdef()
        enriched = t.enrich(hdef, created_by="operator", source_format="yaml")
        prov = enriched["provenance"]
        assert prov["created_by"] == "operator"
        assert prov["normalized_from"] == "yaml"
        assert prov["schema_version"] == "heuristic_definition.v1"
        assert "normalized_at" in prov

    def test_enrich_preserves_derived_from(self):
        t = self._tracker()
        hdef = _make_valid_hdef()
        enriched = t.enrich(hdef, derived_from="old_heuristic_v1")
        assert enriched["provenance"]["derived_from"] == "old_heuristic_v1"

    def test_verify_valid_hash(self):
        t = self._tracker()
        hdef = _make_valid_hdef()
        enriched = t.enrich(hdef)
        result = t.verify(enriched)
        assert result.valid

    def test_verify_tampered_hash_fails(self):
        t = self._tracker()
        hdef = _make_valid_hdef()
        enriched = t.enrich(hdef)
        enriched["content_hash"] = "0" * 64  # tamper
        result = t.verify(enriched)
        assert not result.valid
        assert result.reason == "content_hash_mismatch"

    def test_verify_missing_hash_fails(self):
        t = self._tracker()
        result = t.verify({"heuristic_id": "x"})
        assert not result.valid
        assert result.reason == "no_content_hash_in_hdef"

    def test_mark_activated_embeds_ref(self):
        t = self._tracker()
        hdef = _make_valid_hdef()
        enriched = t.enrich(hdef)
        activated = t.mark_activated(enriched, activation_ref="audit-abc-123")
        assert activated["provenance"]["activation_ref"] == "audit-abc-123"

    def test_extract_record(self):
        t = self._tracker()
        hdef = _make_valid_hdef()
        enriched = t.enrich(hdef, created_by="ananta-worker")
        record = t.extract_record(enriched)
        assert record.created_by == "ananta-worker"
        assert record.content_hash == enriched["content_hash"]

    def test_hash_changes_when_content_changes(self):
        t = self._tracker()
        hdef1 = _make_valid_hdef(description="version one")
        hdef2 = _make_valid_hdef(description="version two")
        e1 = t.enrich(hdef1)
        e2 = t.enrich(hdef2)
        assert e1["content_hash"] != e2["content_hash"]

    def test_provenance_record_roundtrip(self):
        from agent.services.heuristic_runtime.provenance_tracker import ProvenanceRecord
        record = ProvenanceRecord(
            created_by="operator",
            normalized_from="yaml",
            content_hash="abc123",
            derived_from="base_heuristic",
            activation_ref="audit-xyz",
        )
        d = record.to_dict()
        restored = ProvenanceRecord.from_dict(d)
        assert restored.created_by == "operator"
        assert restored.derived_from == "base_heuristic"
        assert restored.activation_ref == "audit-xyz"


# ─────────────────────────────────────────────────────────────────────────────
# T08.01 — CLI heuristic commands (smoke tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestHeuristicCliCommands:
    def test_list_no_index_returns_error(self, tmp_path):
        from agent.cli.commands.heuristic import dispatch
        rc = dispatch(["list", "--index", str(tmp_path / "missing.json")])
        assert rc == 1

    def test_list_with_real_index(self):
        from agent.cli.commands.heuristic import dispatch
        rc = dispatch(["list"])
        assert rc == 0  # heuristics/index.json exists in the project

    def test_list_domain_filter(self):
        from agent.cli.commands.heuristic import dispatch
        rc = dispatch(["list", "--domain", "chat_codecompass"])
        assert rc == 0

    def test_list_as_json(self, capsys):
        from agent.cli.commands.heuristic import dispatch
        rc = dispatch(["list", "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "heuristics" in data

    def test_show_unknown_id_returns_error(self):
        from agent.cli.commands.heuristic import dispatch
        rc = dispatch(["show", "nonexistent_heuristic_xyz"])
        assert rc == 1

    def test_show_known_id(self):
        from agent.cli.commands.heuristic import dispatch
        rc = dispatch(["show", "chat_codecompass_symbol_lookup_default"])
        assert rc == 0

    def test_validate_existing_file(self):
        from agent.cli.commands.heuristic import dispatch
        import pathlib
        active_dir = pathlib.Path(__file__).parent.parent / "heuristics" / "active"
        first_file = sorted(active_dir.glob("*.heuristic.json"))[0]
        rc = dispatch(["validate", str(first_file)])
        assert rc == 0

    def test_validate_nonexistent_file(self):
        from agent.cli.commands.heuristic import dispatch
        rc = dispatch(["validate", "/nonexistent/file.json"])
        assert rc == 1

    def test_normalize_json_file(self, tmp_path, capsys):
        from agent.cli.commands.heuristic import dispatch
        hdef = _make_valid_hdef()
        json_file = tmp_path / "test.json"
        json_file.write_text(json.dumps(hdef))
        rc = dispatch(["normalize", str(json_file)])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["heuristic_id"] == "test_heuristic_one"

    def test_catalog_validates_active_dir(self):
        from agent.cli.commands.heuristic import dispatch
        rc = dispatch(["catalog"])
        assert rc == 0

    def test_no_subcommand_prints_help(self, capsys):
        from agent.cli.commands.heuristic import dispatch
        rc = dispatch([])
        assert rc == 0

    def test_dispatch_help(self, capsys):
        from agent.cli.commands.heuristic import dispatch
        rc = dispatch(["--help"])
        assert rc == 0


# ─────────────────────────────────────────────────────────────────────────────
# T08.02 — HeuristicFormatTuiView
# ─────────────────────────────────────────────────────────────────────────────

class TestHeuristicFormatTuiView:
    def test_build_with_real_catalog(self):
        from agent.services.heuristic_runtime.heuristic_format_tui_view import HeuristicFormatTuiView
        view = HeuristicFormatTuiView()
        model = view.build()
        assert model.active_count > 0
        assert len(model.rows) > 0

    def test_render_returns_string(self):
        from agent.services.heuristic_runtime.heuristic_format_tui_view import HeuristicFormatTuiView
        view = HeuristicFormatTuiView()
        output = view.render()
        assert isinstance(output, str)
        assert "HEURISTIC FORMAT STATUS" in output

    def test_render_contains_active_count(self):
        from agent.services.heuristic_runtime.heuristic_format_tui_view import HeuristicFormatTuiView
        view = HeuristicFormatTuiView()
        output = view.render()
        assert "Active:" in output

    def test_build_empty_catalog(self, tmp_path):
        from agent.services.heuristic_runtime.heuristic_format_tui_view import HeuristicFormatTuiView
        (tmp_path / "active").mkdir()
        (tmp_path / "index.json").write_text('{"version": "0", "heuristics": []}')
        view = HeuristicFormatTuiView(base_path=str(tmp_path))
        model = view.build()
        assert model.active_count == 0
        assert model.rows == []

    def test_to_dict_structure(self):
        from agent.services.heuristic_runtime.heuristic_format_tui_view import HeuristicFormatTuiView
        view = HeuristicFormatTuiView()
        model = view.build()
        d = model.to_dict()
        assert "active_count" in d
        assert "rows" in d
        assert isinstance(d["rows"], list)


# ─────────────────────────────────────────────────────────────────────────────
# T08.04 — E2E: YAML draft → normalize → validate → candidate
# ─────────────────────────────────────────────────────────────────────────────

class TestE2eYamlToCandidate:
    """Full flow: YAML authoring draft → normalize → format-validate → guardrails → candidate JSON."""

    def test_full_flow_declarative_heuristic(self, tmp_path):
        from agent.services.heuristic_runtime.yaml_importer import HeuristicYamlImporter
        from agent.services.heuristic_runtime.format_validator import HeuristicFormatValidator
        from agent.services.heuristic_runtime.ai_proposal_guardrails import AiProposalGuardrails
        from agent.services.heuristic_runtime.provenance_tracker import HeuristicProvenanceTracker

        # Step 1 — write YAML draft to authoring/
        authoring_dir = tmp_path / "authoring"
        authoring_dir.mkdir()
        yaml_file = authoring_dir / "yaml_test_heuristic.heuristic.yaml"
        yaml_file.write_text(MINIMAL_YAML)

        # Step 2 — import (normalize)
        importer = HeuristicYamlImporter(base_path=str(tmp_path))
        import_result = importer.import_file(str(yaml_file))
        assert import_result.success, f"Import failed: {import_result.reason_code}"
        assert import_result.heuristic_id == "yaml_test_heuristic"
        assert os.path.isfile(import_result.candidate_path)

        with open(import_result.candidate_path) as f:
            candidate = json.load(f)

        # Step 3 — format validate
        fv = HeuristicFormatValidator()
        fv_result = fv.validate(candidate)
        assert fv_result.passed, f"Format validation failed: {fv_result.reason_codes}"

        # Step 4 — AI guardrails (add provenance to simulate AI authoring)
        candidate["provenance"] = {"created_by": "ananta-worker"}
        guard = AiProposalGuardrails()
        guard_result = guard.check(candidate)
        assert guard_result.passed, f"Guardrails failed: {guard_result.rejection_reasons}"

        # Step 5 — enrich provenance + verify hash
        tracker = HeuristicProvenanceTracker()
        enriched = tracker.enrich(candidate, created_by="ananta-worker", source_format="yaml")
        verify_result = tracker.verify(enriched)
        assert verify_result.valid, f"Hash verification failed: {verify_result.reason}"

        # Final checks
        assert enriched["status"] == "candidate"
        assert enriched["heuristic_id"] == "yaml_test_heuristic"
        assert len(enriched["content_hash"]) == 64

    def test_full_flow_python_strategy_heuristic(self, tmp_path):
        from agent.services.heuristic_runtime.yaml_importer import HeuristicYamlImporter
        from agent.services.heuristic_runtime.format_validator import HeuristicFormatValidator
        from agent.services.heuristic_runtime.ai_proposal_guardrails import AiProposalGuardrails

        authoring_dir = tmp_path / "authoring"
        authoring_dir.mkdir()
        yaml_file = authoring_dir / "yaml_py_heuristic.heuristic.yaml"
        yaml_file.write_text(PYTHON_STRATEGY_YAML)

        importer = HeuristicYamlImporter(base_path=str(tmp_path))
        import_result = importer.import_file(str(yaml_file))
        assert import_result.success

        with open(import_result.candidate_path) as f:
            candidate = json.load(f)

        fv = HeuristicFormatValidator()
        fv_result = fv.validate(candidate)
        assert fv_result.passed, f"Format validation failed: {fv_result.reason_codes}"

        candidate["provenance"] = {"created_by": "ananta-worker"}
        guard = AiProposalGuardrails()
        guard_result = guard.check(candidate)
        assert guard_result.passed, f"Guardrails failed: {guard_result.rejection_reasons}"

    def test_e2e_rejects_active_status_in_yaml(self, tmp_path):
        from agent.services.heuristic_runtime.yaml_importer import HeuristicYamlImporter

        yaml_with_active = MINIMAL_YAML + "\nstatus: active\n"
        authoring_dir = tmp_path / "authoring"
        authoring_dir.mkdir()
        yaml_file = authoring_dir / "yaml_test_heuristic.heuristic.yaml"
        yaml_file.write_text(yaml_with_active)

        importer = HeuristicYamlImporter(base_path=str(tmp_path))
        result = importer.import_file(str(yaml_file))
        assert result.success  # import succeeds but forces candidate
        assert any("yaml_source_cannot_be_active" in w for w in result.warnings)

        with open(result.candidate_path) as f:
            candidate = json.load(f)
        assert candidate["status"] == "candidate"

    def test_e2e_guardrails_block_inline_code(self, tmp_path):
        from agent.services.heuristic_runtime.yaml_importer import HeuristicYamlImporter
        from agent.services.heuristic_runtime.ai_proposal_guardrails import AiProposalGuardrails

        authoring_dir = tmp_path / "authoring"
        authoring_dir.mkdir()
        yaml_file = authoring_dir / "yaml_test_heuristic.heuristic.yaml"
        yaml_file.write_text(MINIMAL_YAML)

        importer = HeuristicYamlImporter(base_path=str(tmp_path))
        result = importer.import_file(str(yaml_file))
        assert result.success

        with open(result.candidate_path) as f:
            candidate = json.load(f)

        # Inject inline_code (simulating a malicious AI)
        candidate["provenance"] = {"created_by": "ananta-worker"}
        candidate["runtime"]["inline_code"] = "import subprocess; subprocess.run(['rm', '-rf', '/'])"

        guard = AiProposalGuardrails()
        guard_result = guard.check(candidate)
        assert not guard_result.passed
        assert "inline_code_field_forbidden" in guard_result.rejection_reasons

    def test_e2e_normalize_json_draft(self, tmp_path):
        """Non-YAML flow: normalize a JSON draft → validate → enrich provenance."""
        from agent.services.heuristic_runtime.heuristic_normalizer import HeuristicNormalizer
        from agent.services.heuristic_runtime.format_validator import HeuristicFormatValidator
        from agent.services.heuristic_runtime.provenance_tracker import HeuristicProvenanceTracker

        raw = {
            "heuristic_id": "json_draft_heuristic",
            "version": "1.0.0",
            "domain": "helpcenter",
            "description": "Helpcenter failure triage from JSON draft.",
            "safety_class": "readonly",
            "capabilities": ["read_local_context"],
            "inputs": ["query"],
            "outputs": ["failure_ref"],
            "parameters": {},
        }

        normalizer = HeuristicNormalizer()
        norm = normalizer.normalize(raw, source_format="json")
        assert norm.success

        normalized = norm.normalized
        fv = HeuristicFormatValidator()
        fv_result = fv.validate(normalized)
        assert fv_result.passed, fv_result.reason_codes

        tracker = HeuristicProvenanceTracker()
        enriched = tracker.enrich(normalized, created_by="operator")
        verify = tracker.verify(enriched)
        assert verify.valid
