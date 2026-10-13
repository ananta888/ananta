"""Independent Hub deployment facts; never a Worker self-attestation."""

from typing import Protocol

from ananta_contracts.pi_runtime_manifest import PiRuntimeManifest


class PiRuntimeDeploymentPort(Protocol):
    def require_manifest(
        self, *, tenant_id: str, project_id: str, worker_id: str, worker_url: str,
    ) -> PiRuntimeManifest:
        """Return pinned, policy-admitted scheduler facts or raise.

        Implementations must independently bind the registered Worker to the
        immutable deployed image/revision and observed versions. Caller or
        Worker supplied manifest bytes cannot implement this authority.
        """
        ...
