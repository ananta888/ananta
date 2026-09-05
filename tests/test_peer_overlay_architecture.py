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


def test_peer_relay_surface_has_no_content_key_or_decryption_capability() -> None:
    relay_paths = sorted((ROOT / "frontend-angular/src/app/services/peer-overlay").glob("*.ts"))
    production_sources = [path for path in relay_paths if not path.name.endswith(".spec.ts")]
    forbidden = ("CryptoKey", ".decrypt(", "exportKey(", "privateKey", "private_key")
    violations = {
        str(path.relative_to(ROOT)): token
        for path in production_sources
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    }
    assert not violations


def test_production_csp_isolates_workers_and_denies_plugin_objects() -> None:
    nginx = (ROOT / "docker/nginx/conf.d/default.conf").read_text(encoding="utf-8")
    csp = next(line for line in nginx.splitlines() if "Content-Security-Policy" in line)
    assert "worker-src 'self' blob:" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
