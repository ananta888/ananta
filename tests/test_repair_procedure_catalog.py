"""DRR-T008: Repair procedure catalog tests."""
from __future__ import annotations

from pydantic import ValidationError
import pytest

from agent.services.repair_procedure_catalog import (
    CatalogEntry,
    CatalogStep,
    get_catalog,
    lookup_catalog,
    validate_command_template,
)


class TestCommandTemplateValidation:
    def test_allowed_params_accepted(self) -> None:
        validate_command_template("lsof -i :{port}")
        validate_command_template("systemctl status {service_name}")
        validate_command_template("journalctl -n {log_lines}")

    def test_disallowed_param_rejected(self) -> None:
        with pytest.raises(ValueError, match="disallowed template parameter"):
            validate_command_template("rm -rf {user_input}")

    def test_safe_literal_command_accepted(self) -> None:
        validate_command_template("docker compose ps")
        validate_command_template("echo port_free")


class TestCatalogStep:
    def test_valid_inspect_step(self) -> None:
        step = CatalogStep(
            step_id="check-port",
            title="Check port",
            command_template="lsof -i :{port}",
            action_safety_class="inspect_only",
        )
        assert step.step_id == "check-port"

    def test_invalid_safety_class_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown action_safety_class"):
            CatalogStep(
                step_id="bad",
                title="Bad",
                action_safety_class="unknown_class",
            )

    def test_no_command_allowed_for_pure_probe(self) -> None:
        step = CatalogStep(
            step_id="probe",
            title="Pure probe step without command template",
        )
        assert step.command_template == ""


class TestCatalogEntry:
    def test_valid_entry(self) -> None:
        entry = CatalogEntry(
            procedure_id="proc-test-v1",
            problem_class="port_conflict",
            steps=[
                CatalogStep(step_id="s1", title="Inspect", action_safety_class="inspect_only"),
            ],
        )
        assert entry.procedure_id == "proc-test-v1"

    def test_invalid_problem_class_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown problem_class"):
            CatalogEntry(
                procedure_id="proc-bad",
                problem_class="unknown_class",
            )

    def test_invalid_safety_class_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown safety_class"):
            CatalogEntry(
                procedure_id="proc-bad",
                problem_class="port_conflict",
                safety_class="unknown",
            )


class TestCatalogLookup:
    def test_get_catalog_returns_entries(self) -> None:
        entries = get_catalog()
        assert len(entries) > 0
        assert all(isinstance(e, CatalogEntry) for e in entries)

    def test_lookup_by_problem_class(self) -> None:
        results = lookup_catalog(problem_class="port_conflict")
        assert len(results) > 0
        assert results[0]["entry"]["problem_class"] == "port_conflict"

    def test_lookup_without_mutation_returns_only_safe(self) -> None:
        results = lookup_catalog(problem_class="service_start_failure", include_mutation=False)
        assert all(r["safety_class"] == "safe" for r in results)

    def test_lookup_with_mutation_includes_all(self) -> None:
        results = lookup_catalog(problem_class="service_start_failure", include_mutation=True)
        assert len(results) > 1

    def test_lookup_unknown_problem_class_returns_empty(self) -> None:
        results = lookup_catalog(problem_class="nonexistent")
        assert results == []

    def test_lookup_with_platform_filter(self) -> None:
        results = lookup_catalog(
            problem_class="port_conflict",
            environment_facts={"platform_target": "ubuntu"},
        )
        assert len(results) > 0

    def test_catalog_entries_have_validation(self) -> None:
        for entry in get_catalog():
            for step in entry.steps:
                if step.command_template:
                    validate_command_template(step.command_template)

    def test_all_catalog_entries_have_procedure_id(self) -> None:
        for entry in get_catalog():
            assert entry.procedure_id
