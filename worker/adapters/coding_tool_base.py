from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

AdapterKind = Literal["native", "optional", "experimental", "unavailable"]


@dataclass(frozen=True)
class AdapterDescriptor:
    adapter_id: str
    display_name: str
    kind: AdapterKind
    enabled: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "enabled": self.enabled,
            "reason": self.reason,
        }


class CodingToolAdapter(Protocol):
    def descriptor(self) -> AdapterDescriptor:
        ...

    def capabilities(self) -> dict[str, bool]:
        ...

    def plan(self, *, task_id: str, capability_id: str, prompt: str) -> dict[str, Any]:
        ...

    def propose_patch(self, *, task_id: str, capability_id: str, prompt: str, base_ref: str = "HEAD") -> dict[str, Any]:
        ...

    def run_tests(self, *, task_id: str, command: str) -> dict[str, Any]:
        ...

    def apply_patch(self, *, task_id: str, patch_artifact: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("apply_patch_not_supported")
