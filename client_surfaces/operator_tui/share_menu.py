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
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(plain)
        if match.start() <= x < next_start:
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

    # Status-Meldung vom Action Executor
    status_msg = str(payload.get("share_status_message") or "")
    if status_msg:
        color = "\x1b[31m" if any(w in status_msg.lower() for w in ("fehler", "fehlgeschlagen", "error")) else "\x1b[32m"
        lines.append(f"  {color}{status_msg}\x1b[0m")
        lines.append("")

    # ── Session-Übersicht ────────────────────────────────────────────────────
    lines.extend(_session_overview_lines(payload, _W))

    lines.append(f"  {_btn(':share help', 'alle Befehle anzeigen')}")
    return lines


def _session_row(s: dict[str, Any], width: int, *, active_id: str = "", show_owner: bool = False) -> str:
    sid = str(s.get("id") or "")[:8]
    title = str(s.get("title") or "Session")
    pcount = len(s.get("participants") or [])
    perms = s.get("permissions") or s.get("allowed_permissions") or {}
    view_flag = " \x1b[32m[view]\x1b[0m" if perms.get("view_tui") else ""
    active_mark = "\x1b[32m●\x1b[0m " if active_id and str(s.get("id") or "") == active_id else "  "
    owner_prefix = ""
    if show_owner:
        owner = str(s.get("owner_user_id") or "")[:12]
        owner_prefix = f"\x1b[90m{owner:<12}\x1b[0m  "
    max_title = max(10, width - 30 - (14 if show_owner else 0))
    title_trunc = title[:max_title]
    return f"  {active_mark}{owner_prefix}\x1b[1m{title_trunc}\x1b[0m \x1b[90m[{sid}] {pcount}P{view_flag}\x1b[0m"


def _session_overview_lines(payload: dict[str, Any], width: int) -> list[str]:
    lines: list[str] = []
    sessions_mine: list[dict[str, Any]] = list(payload.get("sessions_mine") or [])
    sessions_joined: list[dict[str, Any]] = list(payload.get("sessions_joined") or [])
    active_id = str((payload.get("selected_session") or {}).get("id") or "")

    # Trennlinie
    def _rule(label: str) -> str:
        pad = max(0, width - len(label) - 4)
        return f"  \x1b[90m── {label} {'─' * pad}\x1b[0m"

    # ── Meine Sessions
    lines.append(_rule(f"Meine Sessions ({len(sessions_mine)})"))
    if sessions_mine:
        for s in sessions_mine[:8]:
            lines.append(_session_row(s, width, active_id=active_id))
        if len(sessions_mine) > 8:
            lines.append(f"  \x1b[90m  … {len(sessions_mine) - 8} weitere\x1b[0m")
        lines.append(
            f"  {_btn(':share invite', 'Invite')}  "
            f"{_btn(':share view on', 'View an')}  "
            f"{_btn(':share stop', 'beenden')}"
        )
    else:
        lines.append("  \x1b[90m(keine)\x1b[0m")
    lines.append(
        f"  {_btn(':share create', 'neue Session')}  "
        f"{_btn(':share list', 'aktualisieren')}"
    )
    lines.append("")

    # ── Beigetreten
    lines.append(_rule(f"Beigetreten ({len(sessions_joined)})"))
    if sessions_joined:
        # Gruppierung nach Owner
        by_owner: dict[str, list[dict[str, Any]]] = {}
        for s in sessions_joined:
            owner = str(s.get("owner_user_id") or "unbekannt")
            by_owner.setdefault(owner, []).append(s)
        for owner, owner_sessions in sorted(by_owner.items()):
            if len(by_owner) > 1:
                lines.append(f"    \x1b[36m{owner}\x1b[0m")
            for s in owner_sessions[:5]:
                lines.append(_session_row(s, width, active_id=active_id, show_owner=len(by_owner) == 1))
        lines.append(f"  {_btn(':share join', 'beitreten')}")
    else:
        lines.append("  \x1b[90m(keine)\x1b[0m")
        lines.append(f"  {_btn(':share join <code>', 'per Invite-Code beitreten')}")
    lines.append("")

    # Teilnehmerliste der ausgewählten Session
    participants: list[dict[str, Any]] = list(payload.get("participants") or [])
    selected = dict(payload.get("selected_session") or {})
    if selected and participants:
        session_title = str(selected.get("title") or "Session")[:28]
        lines.append(_rule(f"Teilnehmer: {session_title}"))
        for p in participants:
            uid = str(p.get("user_id") or "")[:18]
            role = str(p.get("role") or "participant")
            revoked = p.get("revoked_at")
            last_seen_raw = p.get("last_seen")
            try:
                last_seen_age = max(0, int(_now() - float(last_seen_raw or 0.0)))
            except Exception:
                last_seen_age = -1
            online = bool(not revoked and (last_seen_age < 90 or last_seen_age < 0))
            dot = "\x1b[31m●\x1b[0m" if revoked else ("\x1b[32m●\x1b[0m" if online else "\x1b[33m●\x1b[0m")
            presence = "revoked" if revoked else ("online" if online else f"offline {last_seen_age}s")
            perms = dict(p.get("permissions") or {})
            cursor_flag = " cursor" if perms.get("remote_cursor") else ""
            lines.append(f"  {dot} \x1b[1m{uid}\x1b[0m \x1b[90m({role}, {presence}{cursor_flag})\x1b[0m")
        lines.append("")

    # Kompakte Audit-Historie
    audit_items: list[dict[str, Any]] = list(payload.get("share_audit_items") or [])
    if audit_items:
        lines.append(_rule("Audit (letzte Events)"))
        for item in audit_items[-5:]:
            ts = str(item.get("ts") or "")[:19].replace("T", " ")
            text = str(item.get("text") or "")[: max(10, width - 10)]
            lines.append(f"  \x1b[90m{ts}\x1b[0m {text}")
        lines.append("")

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
