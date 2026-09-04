"""Hub-side assignment builder; execution remains delegated to one Worker."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent.services.verification_profiles import VerificationProfileService
from ananta_contracts.verification import VerificationAssignmentV1


class VerificationOrchestrationService:
    def __init__(self, profiles: VerificationProfileService) -> None:
        self._profiles = profiles

    def build_assignment(
        self,
        *,
        evidence_assignment: Mapping[str, object],
        repository_revision: str,
        profile_id: str,
        toolchain_digest: str,
        target_symbols: Sequence[str],
    ) -> VerificationAssignmentV1:
        """Bind an already Hub-issued evidence assignment to one closed run.

        This service deliberately has no ID generator.  The Evidence Registry
        must reserve SRC_/RUN_ identities before this method is called.
        """

        profile = self._profiles.get_enabled(profile_id)
        return VerificationAssignmentV1(
            evidence_assignment=evidence_assignment,
            repository_revision=repository_revision,
            profile_id=profile.profile_id,
            profile_digest=profile.digest,
            toolchain_digest=toolchain_digest,
            backend=profile.backend,
            target_symbols=target_symbols,
            budgets=profile.budgets,
        )


__all__ = ["VerificationOrchestrationService"]
