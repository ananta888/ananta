"""Hub policy for bounded peer-overlay operation during partitions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PeerOverlayOfflineProfile:
    name: str
    maximum_grace_seconds: int


class PeerOverlayOfflineAuthorityPolicy:
    """Resolves a closed security profile; peers cannot extend this lease."""

    _PROFILES = {
        "strict": PeerOverlayOfflineProfile("strict", 30),
        "balanced": PeerOverlayOfflineProfile("balanced", 60),
        "availability": PeerOverlayOfflineProfile("availability", 120),
    }

    def resolve(self, profile: str, requested_seconds: int | None = None) -> PeerOverlayOfflineProfile:
        selected = self._PROFILES.get(str(profile or "").strip())
        if selected is None:
            raise ValueError("peer_overlay_offline_profile_invalid")
        if requested_seconds is None:
            return selected
        if not isinstance(requested_seconds, int) or isinstance(requested_seconds, bool) or requested_seconds < 0:
            raise ValueError("peer_overlay_offline_grace_invalid")
        return PeerOverlayOfflineProfile(selected.name, min(requested_seconds, selected.maximum_grace_seconds))


__all__ = ["PeerOverlayOfflineAuthorityPolicy", "PeerOverlayOfflineProfile"]
