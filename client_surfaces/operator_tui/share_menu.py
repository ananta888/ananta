"""SS02.02 / SS03.01: Share / Teilnehmer TUI-Menü.

Zeigt OIDC-Status, Device-Key-Status, aktive Share-Sessions und Teilnehmer.
Klickbare Buttons im Format  [▶ :command]  — erkennbar für den Maus-Click-Handler.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.device_keys import DeviceKeyError, get_device_key_manager
from client_surfaces.operator_tui.network_profile import get_active_profile, is_public_profile_active

_ANSI = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;?]*[ -/]*[@-~])")
# Muster für Maus-Click-Handler: [▶ :command]
_BTN_PATTERN = re.compile(r"\[▶ (:[^\]]+)\]")


def _now() -> float:
    return time.time()


def _btn(cmd: str, label: str | None = None) -> str:
    """Rendert einen klickbaren Button. Maus-Click-Handler erkennt [▶ :cmd]."""
    display = label or cmd
    return f"\x1b[36m[▶ {cmd}]\x1b[0m \x1b[90m{display}\x1b[0m"


def extract_click_command(rendered_line: str, *, x: int | None = None) -> str | None:
    """Extrahiert :command aus einer Button-Zeile. None wenn kein Button getroffen wurde."""
    plain = _ANSI.sub("", rendered_line)
    matches = list(_BTN_PATTERN.finditer(plain))
    if x is None:
        return matches[0].group(1).strip() if matches else None
    for match in matches:
        if match.start() <= x < match.end():
            return match.group(1).strip()
    return None


def build_share_section_lines(
    payload: dict[str, Any],
    *,
    width: int = 80,
    selected_index: int = 0,
) -> list[str]:
    """Rendert den Inhalt der Share/Teilnehmer-Section."""
    lines: list[str] = []
    _W = max(40, width - 4)

    # Netzwerkprofil
    profile = get_active_profile()
    profile_id = str(profile.get("profile_id") or "local")
    if is_public_profile_active():
        lines.append("  \x1b[33m[!] PUBLIC RENDEZVOUS: keycloak.ananta.de + webrtc.ananta.de\x1b[0m")
        lines.append("  \x1b[33m    Routing-Metadaten sichtbar. Inhalte E2E-verschlüsselt.\x1b[0m")
    else:
        lines.append(f"  Profil: \x1b[36m{profile_id}\x1b[0m   {_btn(':oidc login', 'OIDC-Login starten')}")
    lines.append("")

    # Aktiver Device Flow
    flow = dict(payload.get("oidc_device_flow") or {})
    if flow.get("status") in ("waiting", "polling"):
        lines.append("  \x1b[1;33m◉ OIDC Login läuft — Browser öffnen:\x1b[0m")
        lines.append(f"  URL:  \x1b[36m{flow.get('verification_uri', '')}\x1b[0m")
        lines.append(f"  Code: \x1b[1;33m{flow.get('user_code', '')}\x1b[0m")
        lines.append("")
    elif flow.get("status") == "error":
        lines.append(f"  \x1b[31m✗ OIDC Fehler: {flow.get('error', '')}\x1b[0m")
        lines.append(f"  {_btn(':oidc login', 'erneut versuchen')}")
        lines.append("")

    # OIDC-Status
    oidc_info = dict(payload.get("oidc_status") or {})
    oidc_user = str(oidc_info.get("username") or oidc_info.get("sub") or "")
    oidc_issuer_str = str(oidc_info.get("issuer") or "")
    if oidc_user:
        lines.append(f"  OIDC: \x1b[32m✓ {oidc_user}\x1b[0m   {_btn(':oidc logout', 'ausloggen')}")
        if oidc_issuer_str:
            lines.append(f"  \x1b[90m{oidc_issuer_str}\x1b[0m")
    else:
        lines.append(f"  OIDC: \x1b[90mnicht eingeloggt\x1b[0m")
        lines.append(f"  {_btn(':oidc login', 'mit Keycloak einloggen')}")
    lines.append("")

    # Device-Key-Status
    mgr = get_device_key_manager()
    if mgr.key_exists():
        try:
            info = mgr.get_public_info()
            fp = str(info.get("fingerprint") or "")
            algo = str(info.get("algorithm") or "")
            lines.append(f"  Device-Key: \x1b[32m✓ {algo}\x1b[0m   {_btn(':share key rotate', 'rotieren')}")
            lines.append(f"  \x1b[90mFingerprint: {fp}\x1b[0m")
        except DeviceKeyError as exc:
            lines.append(f"  Device-Key: \x1b[31mFEHLER: {exc}\x1b[0m")
            lines.append(f"  {_btn(':share key generate', 'Key neu erstellen')}")
    else:
        lines.append("  Device-Key: \x1b[90mkein Key vorhanden\x1b[0m")
        lines.append(f"  {_btn(':share key generate', 'lokalen Device-Key erstellen')}")
    lines.append("")

    # Aktive Share-Sessions
    sessions: list[dict[str, Any]] = list(payload.get("sessions") or [])
    if sessions:
        lines.append(f"  Sessions ({len(sessions)}):   {_btn(':share stop', 'beenden')}")
        for s in sessions[:5]:
            title = str(s.get("title") or "Session")[:_W - 22]
            sid = str(s.get("id") or "")[:8]
            pcount = len(s.get("participants") or [])
            perms = s.get("permissions") or s.get("allowed_permissions") or {}
            view_flag = " \x1b[32m[view]\x1b[0m" if perms.get("view_tui") else ""
            lines.append(f"    \x1b[1m{title}\x1b[0m \x1b[90m[{sid}] {pcount} Teilnehmer{view_flag}\x1b[0m")
        lines.append(f"  {_btn(':share invite', 'Invite-Code anzeigen')}")
        lines.append(f"  {_btn(':share view on', 'TUI-View freigeben')}  {_btn(':share view off', 'View sperren')}")
    else:
        lines.append("  Keine aktiven Sessions")
        lines.append(f"  {_btn(':share create', 'neue Session erstellen')}")
        lines.append(f"  {_btn(':share join', 'per Invite-Code beitreten')}")
    lines.append("")

    # Teilnehmerliste
    selected_session = dict(payload.get("selected_session") or {})
    participants: list[dict[str, Any]] = list(payload.get("participants") or [])
    if selected_session and participants:
        session_title = str(selected_session.get("title") or "Session")[:30]
        lines.append(f"  Teilnehmer in \x1b[1m{session_title}\x1b[0m:")
        for p in participants:
            user_id = str(p.get("user_id") or "")[:16]
            fp = str(p.get("public_key_fingerprint") or "")
            role = str(p.get("role") or "participant")
            revoked = p.get("revoked_at")
            status_str = "\x1b[31m[revoked]\x1b[0m" if revoked else "\x1b[32m[aktiv]\x1b[0m"
            fp_short = f"\x1b[90m{fp[:17]}…\x1b[0m" if fp else "\x1b[33m[kein Key]\x1b[0m"
            lines.append(f"    {status_str} {user_id} ({role}) {fp_short}")
        lines.append("")

    # Status-Meldung vom Action Executor
    status_msg = str(payload.get("share_status_message") or "")
    if status_msg:
        color = "\x1b[31m" if any(w in status_msg.lower() for w in ("fehler", "fehlgeschlagen", "error")) else "\x1b[32m"
        lines.append(f"  {color}{status_msg}\x1b[0m")
        lines.append("")

    lines.append(f"  {_btn(':share help', 'alle Befehle anzeigen')}")
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
    is_pem = value.startswith("-----BEGIN") or value.startswith("STUB:")
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
