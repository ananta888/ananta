"""Independent fail-closed release decisions for peer-overlay transport paths."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ananta_contracts.peer_overlay import OverlayReleaseStage

_SOURCE_REF = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")
_RUN_REF = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9_.:-]{2,255}$")


class PeerOverlayReleaseGate:
    """Keeps mesh, data overlay and media overlay decisions separable."""

    PATH_GATES = {
        "mesh": frozenset({"contracts", "browser", "resource", "fallback"}),
        "data_overlay": frozenset({"contracts", "security", "churn", "backpressure", "fallback"}),
        "media_overlay": frozenset({"contracts", "standards", "browser", "security", "nat", "quality", "fallback"}),
    }

    def evaluate(
        self,
        *,
        path: str,
        requested_stage: str,
        local_gates: Mapping[str, bool],
        source_refs: list[str],
        run_refs: list[str],
        allowed_source_refs: frozenset[str] = frozenset(),
        allowed_run_refs: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        required = self.PATH_GATES.get(path)
        if required is None:
            raise ValueError("peer_overlay_release_path_invalid")
        stage = OverlayReleaseStage(requested_stage)
        reasons = ["peer_overlay_local_gates_incomplete"] if any(not local_gates.get(key) for key in required) else []
        if not source_refs or any(
            not _SOURCE_REF.fullmatch(value) or value not in allowed_source_refs for value in source_refs
        ):
            reasons.append("peer_overlay_authoritative_source_evidence_unavailable")
        if not run_refs or any(not _RUN_REF.fullmatch(value) or value not in allowed_run_refs for value in run_refs):
            reasons.append("peer_overlay_runtime_evidence_unavailable")
        if path == "media_overlay":
            reasons.append("peer_overlay_cross_peer_media_standard_no_go")
        if stage == OverlayReleaseStage.DISABLED:
            reasons.append("peer_overlay_release_stage_disabled")
        reasons = list(dict.fromkeys(reasons))
        allowed = not reasons
        return {
            "path": path,
            "requested_stage": stage.value,
            "release_allowed": allowed,
            "state": "passed" if allowed else "blocked",
            "reason_codes": reasons,
            "local_gates": {key: bool(local_gates.get(key)) for key in sorted(required)},
            "source_refs": list(source_refs) if allowed else [],
            "run_refs": list(run_refs) if allowed else [],
            "fallback": "livekit_e2ee" if path in {"mesh", "media_overlay"} else "hub_control_only",
            "human_intervention_required": False,
        }


__all__ = ["PeerOverlayReleaseGate"]
