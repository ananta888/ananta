from __future__ import annotations

from client_surfaces.freecad.bridge.ananta_freecad_bridge import build_freecad_bridge_envelope, validate_freecad_bridge_envelope
from client_surfaces.freecad.workbench import register, unregister, workbench_info
from client_surfaces.freecad.workbench.InitGui import WORKBENCH


def run_smoke() -> dict[str, object]:
    envelope = build_freecad_bridge_envelope(
        capability_id="freecad.document.read",
        action_id="capture_context",
        payload={"mode": "smoke"},
        session_id="smoke-session",
        correlation_id="smoke-correlation",
    )
    return {
        "info": workbench_info,
        "register": register(),
        "unregister": unregister(),
        "commands": WORKBENCH.Initialize(),
        "bridge_errors": validate_freecad_bridge_envelope(envelope),
    }
