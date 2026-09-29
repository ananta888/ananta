from __future__ import annotations

from collections.abc import Mapping

import pytest

from agent.services.business_controlling_runtime_control import (
    BusinessControllingRuntimeControlService,
    JsonBusinessControllingRuntimeControlRepository,
)
from agent.services.business_controlling_workbench_service import (
    BusinessControllingWorkbenchError,
    BusinessControllingWorkbenchService,
    JsonBusinessControllingFindingStore,
)


class _Imports:
    def profile_import(self, **_: object) -> Mapping[str, object]:
        return {"profile_digest": "a" * 64}

    def confirm_mapping(self, **_: object) -> Mapping[str, object]:
        return {"confirmation_digest": "b" * 64}


class _Analysis:
    def __init__(self, *, sensitive: bool = False) -> None:
        self.sensitive = sensitive
        self.calls: list[tuple[bool, bool]] = []

    def execute(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append(
            (bool(kwargs["statistics_enabled"]), bool(kwargs["explanations_enabled"]))
        )
        finding: dict[str, object] = {
            "finding_id": "finding-a",
            "kind": "deterministic_violation",
            "severity": "high",
            "dataset_version": "dataset-a",
            "rule_version": "v1",
            "confidence": None,
            "evidence_digest": "c" * 64,
            "disposition": "open",
            "revision": 0,
        }
        if self.sensitive:
            finding["raw_value"] = "must-not-persist"
        return {
            "run_id": "run-a",
            "status": "completed",
            "finding_count": 1,
            "findings": [finding],
        }


def _service(tmp_path, *, analysis: _Analysis | None = None):
    runtime = JsonBusinessControllingRuntimeControlRepository(tmp_path / "runtime.json")
    BusinessControllingRuntimeControlService(runtime).replace(
        expected_revision=0,
        global_enabled=True,
        statistical_enabled=False,
        explanations_enabled=True,
        disabled_catalog_entry_ids=(),
        actor_id="automation",
        reason="rules-only",
    )
    store_path = tmp_path / "findings.json"
    service = BusinessControllingWorkbenchService(
        runtime_control=runtime,
        imports=_Imports(),
        analysis=analysis or _Analysis(),
        findings=JsonBusinessControllingFindingStore(store_path),
    )
    return service, store_path


def test_workbench_persists_run_occ_disposition_and_redacted_export(tmp_path) -> None:
    analysis = _Analysis()
    service, store_path = _service(tmp_path, analysis=analysis)
    scope = {"tenant_id": "tenant-a", "project_id": "project-a"}

    run = service.start_run(
        **scope,
        actor_id="automation",
        request_payload={
            "statistics_enabled": False,
            "explanations_enabled": True,
        },
    )
    updated = service.set_disposition(
        **scope,
        actor_id="automation",
        finding_id="finding-a",
        disposition="confirmed",
        expected_revision=0,
    )
    report = service.export_findings(**scope, actor_id="automation")

    assert run == {"run_id": "run-a", "status": "completed", "finding_count": 1}
    assert analysis.calls == [(False, True)]
    assert updated["revision"] == 1
    assert report["content_redacted"] is True
    assert "tenant-a" not in str(report)
    assert "tenant-a" in store_path.read_text()


def test_runtime_switches_and_sensitive_payloads_fail_closed(tmp_path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(BusinessControllingWorkbenchError, match="statistics_runtime_disabled"):
        service.start_run(
            tenant_id="tenant-a",
            project_id="project-a",
            actor_id="automation",
            request_payload={
                "statistics_enabled": True,
                "explanations_enabled": True,
            },
        )

    other_path = tmp_path / "sensitive"
    other_path.mkdir()
    unsafe, _ = _service(other_path, analysis=_Analysis(sensitive=True))
    with pytest.raises(BusinessControllingWorkbenchError, match="finding_shape_invalid"):
        unsafe.start_run(
            tenant_id="tenant-a",
            project_id="project-a",
            actor_id="automation",
            request_payload={
                "statistics_enabled": False,
                "explanations_enabled": True,
            },
        )
