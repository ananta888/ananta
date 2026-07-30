"""Deterministic restricted-inference capability inventory and contract probes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from agent.services.model_inference_adapter_registry import ModelInferenceAdapterRegistry
from agent.services.model_inference_adapters import BaseInferenceAdapter

CapabilityProbe = Callable[[BaseInferenceAdapter], Any]


@dataclass(frozen=True)
class CapabilityProbeFailure:
    capability: str
    reason_code: str

    def to_dict(self) -> dict[str, str]:
        return {
            "capability": self.capability,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class AdapterContractProbeReport:
    adapter_class: str
    engine: str
    checked_capabilities: tuple[str, ...]
    failures: tuple[CapabilityProbeFailure, ...]
    status: str

    @property
    def passed(self) -> bool:
        return self.status == "ready" and not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_class": self.adapter_class,
            "checked_capabilities": list(self.checked_capabilities),
            "engine": self.engine,
            "failures": [item.to_dict() for item in self.failures],
            "passed": self.passed,
            "status": self.status,
        }


class CapabilityContractError(AssertionError):
    """Raised when a ready adapter contradicts its capability declaration."""


def build_capability_inventory(registry: ModelInferenceAdapterRegistry) -> dict[str, Any]:
    """Build a host-independent inventory with a descriptor-source digest."""

    source = {
        "adapters": [descriptor.to_dict() for descriptor in registry.descriptors()],
        "schema_version": "ananta.model-inference-capabilities.v1",
    }
    source_bytes = _canonical_json_bytes(source)
    return {
        **source,
        "source_digest": f"sha256:{hashlib.sha256(source_bytes).hexdigest()}",
    }


def canonical_capability_inventory_json(registry: ModelInferenceAdapterRegistry) -> bytes:
    """Serialize the inventory byte-identically for an unchanged descriptor set."""

    return _canonical_json_bytes(build_capability_inventory(registry)) + b"\n"


def probe_ready_adapter_contract(
    adapter: BaseInferenceAdapter,
    *,
    probes: Mapping[str, CapabilityProbe] | None = None,
) -> AdapterContractProbeReport:
    """Execute deterministic probes for every capability a ready adapter claims."""

    status = adapter.status()
    descriptor = type(adapter).capability_descriptor()
    checked = tuple(sorted(status.capabilities)) if status.status == "ready" else ()
    failures: list[CapabilityProbeFailure] = []
    available_probes = dict(_DEFAULT_PROBES)
    available_probes.update(probes or {})

    for capability in checked:
        declaration = descriptor.capability(capability)
        if declaration.support == "unsupported":
            failures.append(CapabilityProbeFailure(capability, "declared_capability_marked_unsupported"))
            continue
        probe = available_probes.get(capability)
        if probe is None:
            failures.append(CapabilityProbeFailure(capability, "capability_probe_unavailable"))
            continue
        try:
            probe(adapter)
        except NotImplementedError:
            failures.append(CapabilityProbeFailure(capability, "advertised_operation_not_implemented"))
        except Exception as exc:
            failures.append(
                CapabilityProbeFailure(
                    capability,
                    f"capability_probe_failed:{type(exc).__name__}",
                )
            )

    return AdapterContractProbeReport(
        adapter_class=descriptor.adapter_class,
        engine=status.engine,
        checked_capabilities=checked,
        failures=tuple(failures),
        status=status.status,
    )


def assert_ready_adapter_contract(
    adapter: BaseInferenceAdapter,
    *,
    probes: Mapping[str, CapabilityProbe] | None = None,
) -> AdapterContractProbeReport:
    report = probe_ready_adapter_contract(adapter, probes=probes)
    if report.status == "ready" and report.failures:
        reasons = ",".join(f"{item.capability}:{item.reason_code}" for item in report.failures)
        raise CapabilityContractError(f"adapter_capability_contract_failed:{reasons}")
    return report


def canonical_probe_report_json(reports: list[AdapterContractProbeReport]) -> bytes:
    payload = {
        "reports": [
            report.to_dict()
            for report in sorted(reports, key=lambda item: (item.engine, item.adapter_class))
        ],
        "schema_version": "ananta.model-inference-capability-probes.v1",
    }
    return _canonical_json_bytes(payload) + b"\n"


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


_DEFAULT_PROBES: Mapping[str, CapabilityProbe] = {
    "choice_scoring": lambda adapter: adapter.score_choices("probe:", [" alpha", " beta"]),
    "classification": lambda adapter: adapter.classify("probe", ["negative", "positive"]),
    "embeddings": lambda adapter: adapter.embed(["probe"]),
    "feature_extraction": lambda adapter: adapter.extract_features("probe"),
    "rerank": lambda adapter: adapter.rerank(
        "probe",
        [{"excerpt": "probe", "path": "probe", "record_id": "probe"}],
    ),
}
