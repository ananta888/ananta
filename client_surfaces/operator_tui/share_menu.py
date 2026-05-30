"""SS02.02 / SS03.01: Share / Teilnehmer TUI-Menü.

Zeigt OIDC-Status, Device-Key-Status, aktive Share-Sessions und Teilnehmer.
Public Key anzeigen/importieren, Invite erstellen/annehmen.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.device_keys import DeviceKeyError, DeviceKeyManager, get_device_key_manager
from client_surfaces.operator_tui.network_profile import get_active_profile, is_public_profile_active


def _now() -> float:
    return time.time()


def build_share_section_lines(
    payload: dict[str, Any],
    *,
    width: int = 80,
    selected_index: int = 0,
) -> list[str]:
    """Rendert den Inhalt der Share/Teilnehmer-Section."""
    lines: list[str] = []
    _W = max(40, width - 4)

    # Netzwerkprofil-Warnung
    profile = get_active_profile()
    profile_id = str(profile.get("profile_id") or "local")
    if is_public_profile_active():
        lines.append("  \x1b[33m[!] PUBLIC RENDEZVOUS: keycloak.ananta.de + webrtc.ananta.de\x1b[0m")
        lines.append("  \x1b[33m    Routing-Metadaten sichtbar für Ananta-Server. Inhalte E2E-verschlüsselt.\x1b[0m")
    else:
        lines.append(f"  Netzwerkprofil: \x1b[36m{profile_id}\x1b[0m")

    lines.append("")

    # Aktiver Device Flow
    flow = dict(payload.get("oidc_device_flow") or {})
    if flow.get("status") in ("waiting", "polling"):
        lines.append(f"  \x1b[1;33m◉ OIDC Login läuft\x1b[0m")
        lines.append(f"  Browser öffnen:  \x1b[36m{flow.get('verification_uri', '')}\x1b[0m")
        lines.append(f"  Code eingeben:   \x1b[1;33m{flow.get('user_code', '')}\x1b[0m")
        lines.append("")
    elif flow.get("status") == "error":
        lines.append(f"  \x1b[31m✗ OIDC Fehler: {flow.get('error', '')}\x1b[0m  :oidc login erneut")
        lines.append("")

    # OIDC-Status
    oidc_info = dict(payload.get("oidc_status") or {})
    oidc_user = str(oidc_info.get("sub") or oidc_info.get("username") or "")
    oidc_issuer = str(oidc_info.get("issuer") or "")
    if oidc_user:
        lines.append(f"  OIDC: \x1b[32m✓ {oidc_user}\x1b[0m")
        if oidc_issuer:
            lines.append(f"        {oidc_issuer}")
    else:
        lines.append("  OIDC: \x1b[90mnicht eingeloggt\x1b[0m")
        lines.append("        :oidc login  oder  Profil wählen")

    lines.append("")

    # Device-Key-Status
    mgr = get_device_key_manager()
    if mgr.key_exists():
        try:
            info = mgr.get_public_info()
            fp = str(info.get("fingerprint") or "")
            algo = str(info.get("algorithm") or "")
            lines.append(f"  Device-Key: \x1b[32m✓ {algo}\x1b[0m")
            lines.append(f"  Fingerprint: \x1b[36m{fp}\x1b[0m")
        except DeviceKeyError as exc:
            lines.append(f"  Device-Key: \x1b[31mFEHLER: {exc}\x1b[0m")
    else:
        lines.append("  Device-Key: \x1b[90mkein Key vorhanden\x1b[0m")
        lines.append("        :share key generate  um einen zu erstellen")

    lines.append("")

    # Aktive Share-Sessions
    sessions: list[dict[str, Any]] = list(payload.get("sessions") or [])
    if sessions:
        lines.append(f"  Aktive Sessions ({len(sessions)}):")
        for s in sessions[:5]:
            title = str(s.get("title") or "Session")[:_W - 20]
            sid = str(s.get("id") or "")[:8]
            pcount = len(s.get("participants") or [])
            perms = s.get("permissions") or {}
            view_flag = " [view]" if perms.get("view_tui") else ""
            lines.append(f"    • {title} [{sid}] {pcount}P{view_flag}")
    else:
        lines.append("  Keine aktiven Share-Sessions")
        lines.append("  \x1b[90m:share create  um eine Session zu erstellen\x1b[0m")

    lines.append("")

    # Teilnehmerliste der ausgewählten Session
    selected_session = dict(payload.get("selected_session") or {})
    participants: list[dict[str, Any]] = list(payload.get("participants") or [])
    if selected_session and participants:
        session_title = str(selected_session.get("title") or "Session")[:30]
        lines.append(f"  Teilnehmer in '{session_title}':")
        for p in participants:
            user_id = str(p.get("user_id") or "")[:16]
            fp = str(p.get("public_key_fingerprint") or "")
            role = str(p.get("role") or "participant")
            perms = p.get("permissions") or {}
            revoked = p.get("revoked_at")
            status = "\x1b[31m[revoked]\x1b[0m" if revoked else "\x1b[32m[aktiv]\x1b[0m"
            fp_short = f"{fp[:17]}…" if len(fp) > 17 else fp
            fp_display = f"\x1b[90m{fp_short}\x1b[0m" if fp else "\x1b[33m[kein Key]\x1b[0m"
            lines.append(f"    {status} {user_id} ({role}) {fp_display}")

    lines.append("")
    # Status-Meldung vom Action Executor
    status_msg = str(payload.get("share_status_message") or "")
    if status_msg:
        color = "\x1b[31m" if "fehler" in status_msg.lower() or "fehlgeschlagen" in status_msg.lower() else "\x1b[32m"
        lines.append(f"  {color}{status_msg}\x1b[0m")
        lines.append("")
    lines.append("  \x1b[90m:oidc login · :share create · :share join <code> · :share help\x1b[0m")

    return lines


def share_section_lines(
    payload: dict[str, Any],
    *,
    width: int = 80,
    selected_index: int = 0,
) -> list[str]:
    try:
        return build_share_section_lines(payload, width=width, selected_index=selected_index)
    except Exception as exc:
        return [f"  Share-Menü Fehler: {exc}"]


def import_public_key(fingerprint_or_pem: str, user_id: str, *, key_store_dir: Path | None = None) -> dict[str, Any]:
    """Importiert einen Public Key eines Teilnehmers. Idempotent."""
    key_store = key_store_dir or _default_key_store()
    key_store.mkdir(parents=True, exist_ok=True)
    value = str(fingerprint_or_pem or "").strip()
    if not value:
        return {"ok": False, "error": "empty_key"}
    # Einfache Validierung: PEM oder hex fingerprint
    is_pem = value.startswith("-----BEGIN") or value.startswith("STUB:")
    key_id = f"{user_id}_{int(_now())}"
    entry = {
        "user_id": user_id,
        "public_key_pem": value if is_pem else "",
        "fingerprint": value if not is_pem else "",
        "imported_at": _now(),
    }
    entry_file = key_store / f"peer_{user_id[:32]}.json"
    if entry_file.exists():
        try:
            existing = json.loads(entry_file.read_text(encoding="utf-8"))
            if existing.get("fingerprint") == entry.get("fingerprint") and existing.get("fingerprint"):
                return {"ok": True, "idempotent": True, "entry": existing}
        except Exception:
            pass
    entry_file.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    return {"ok": True, "idempotent": False, "entry": entry}


def _default_key_store() -> Path:
    import os
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "ananta" / "peer-keys"
