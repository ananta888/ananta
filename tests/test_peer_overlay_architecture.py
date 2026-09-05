from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_peer_overlay_components_do_not_depend_on_direct_or_livekit_facades() -> None:
    paths = [
        *sorted((ROOT / "agent/services").glob("peer_overlay*.py")),
        *sorted((ROOT / "frontend-angular/src/app/services/peer-overlay").glob("*.ts")),
    ]
    forbidden = ("WebrtcSessionService", "LivekitSfuRoomAdapter", "livekit-client")
    violations = {
        str(path.relative_to(ROOT)): token
        for path in paths
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    }
    assert not violations


def test_peer_overlay_responsibility_matrix_names_focused_boundaries() -> None:
    document = (ROOT / "docs/architecture/peer-overlay-boundaries.md").read_text(encoding="utf-8")
    for boundary in (
        "PeerOverlayControlService",
        "PeerOverlayTopologyService",
        "PeerOverlayStateStore",
        "PeerOverlayRelayHealthPolicy",
        "MultiPeerConnectionManager",
        "PeerOverlayDataRelay",
        "MediaFrameCryptoPort",
        "PeerOverlayReleaseGate",
    ):
        assert boundary in document
