from flask import current_app, g, has_request_context

from agent.tools import registry


def _check_file_access(path: str, operation: str = "read") -> tuple[bool, str]:
    from agent.services.platform_governance_service import get_platform_governance_service
    gov = get_platform_governance_service()
    cfg = current_app.config.get("AGENT_CONFIG")
    if not gov.evaluate_action_pack_access("file", cfg):
        return False, "Action Pack 'file' ist deaktiviert."

    import os

    def _within(child: str, parent: str) -> bool:
        try:
            return os.path.commonpath([child, parent]) == parent
        except Exception:
            return False

    abs_path = os.path.realpath(os.path.abspath(path))
    cwd = os.path.realpath(os.path.abspath("."))
    tmp_root = os.path.realpath(os.path.abspath("/tmp"))
    allowed = _within(abs_path, cwd) or _within(abs_path, tmp_root)
    if not allowed:
        try:
            if has_request_context():
                workspace_dir = str(g.get("workspace_dir", "")).strip()
                if workspace_dir:
                    workspace_real = os.path.realpath(os.path.abspath(workspace_dir))
                    if _within(abs_path, workspace_real):
                        allowed = True
        except Exception:
            pass
    if not allowed:
        return False, f"Zugriff auf Pfad '{path}' verweigert (außerhalb des Workspaces)."

    return True, ""


def _resolve_workspace_path(path: str) -> str:
    import os

    raw = str(path or "").strip()
    if not raw:
        return raw
    if os.path.isabs(raw):
        return raw
    if has_request_context():
        workspace_dir = str(g.get("workspace_dir") or "").strip()
        if workspace_dir:
            return os.path.abspath(os.path.join(workspace_dir, raw))
    return os.path.abspath(raw)


def _workspace_file_hint() -> str:
    import os

    target = None
    if has_request_context():
        target = g.get("workspace_dir") or os.path.abspath(".")
    else:
        target = os.path.abspath(".")
    try:
        files = os.listdir(target)
    except Exception:
        return " Verfuegbare Workspace-Eintraege: (nicht lesbar)."
    hint_files = [f for f in files if os.path.isfile(os.path.join(target, f))][:15]
    hint_dirs = [f + "/" for f in files if os.path.isdir(os.path.join(target, f))][:10]
    hint = hint_files + hint_dirs
    has_ananta = os.path.isdir(os.path.join(target, ".ananta"))
    if has_ananta:
        hint.append(".ananta/ (Task-Kontext: task-brief.md, hub-context.md, ...)")
    if not hint:
        return ""
    return f" Verfuegbare Workspace-Eintraege: {', '.join(hint)}."


@registry.register(
    name="file_read",
    description="Liest den Inhalt einer Datei.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Pfad zur Datei"},
            "encoding": {"type": "string", "description": "Encoding", "default": "utf-8"},
        },
        "required": ["path"],
    },
)
def file_read_tool(path: str = "", encoding: str = "utf-8", file_path: str = "", filename: str = ""):
    resolved = path or file_path or filename
    if not resolved:
        hint = _workspace_file_hint()
        return {"error": f"Parameter 'path' fehlt.{hint}"}
    resolved = _resolve_workspace_path(resolved)
    ok, err = _check_file_access(resolved, "read")
    if not ok: return {"error": err}

    import os
    if not os.path.exists(resolved):
        return {"error": f"Datei '{resolved}' nicht gefunden."}
    try:
        with open(resolved, "r", encoding=encoding) as f:
            content = f.read()
            from agent.common.audit import log_audit
            log_audit("file_read", {"path": resolved, "size": len(content)})
            return {"content": content, "path": resolved}
    except Exception as e:
        return {"error": f"Fehler beim Lesen der Datei: {e}"}


@registry.register(
    name="file_write",
    description="Schreibt Inhalt in eine Datei (ueberschreibt existierende).",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Pfad zur Datei"},
            "content": {"type": "string", "description": "Inhalt"},
            "encoding": {"type": "string", "description": "Encoding", "default": "utf-8"},
        },
        "required": ["path", "content"],
    },
)
def file_write_tool(path: str = "", content: str = "", encoding: str = "utf-8", file_path: str = "", filename: str = ""):
    resolved = path or file_path or filename
    if not resolved:
        hint = _workspace_file_hint()
        return {"error": f"Parameter 'path' fehlt.{hint}"}
    resolved = _resolve_workspace_path(resolved)
    ok, err = _check_file_access(resolved, "write")
    if not ok: return {"error": err}

    import os
    try:
        os.makedirs(os.path.dirname(os.path.abspath(resolved)), exist_ok=True)
        with open(resolved, "w", encoding=encoding) as f:
            f.write(content)
            from agent.common.audit import log_audit
            log_audit("file_write", {"path": resolved, "size": len(content)})
            return {"status": "success", "path": resolved, "size": len(content)}
    except Exception as e:
        return {"error": f"Fehler beim Schreiben der Datei: {e}"}


@registry.register(
    name="file_list",
    description="Listet Dateien in einem Verzeichnis auf.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Pfad zum Verzeichnis", "default": "."},
            "recursive": {"type": "boolean", "description": "Rekursiv auflisten", "default": False},
        },
    },
)
def file_list_tool(path: str = ".", recursive: bool = False):
    resolved = _resolve_workspace_path(path)
    ok, err = _check_file_access(resolved, "read")
    if not ok: return {"error": err}

    import os
    if not os.path.exists(resolved):
        return {"error": f"Verzeichnis '{resolved}' nicht gefunden."}

    try:
        files = []
        if recursive:
            for root, dirs, filenames in os.walk(resolved):
                for f in filenames:
                    files.append(os.path.relpath(os.path.join(root, f), resolved))
        else:
            files = os.listdir(resolved)
        return {"files": files, "path": resolved}
    except Exception as e:
        return {"error": f"Fehler beim Auflisten: {e}"}


@registry.register(
    name="file_patch",
    description="Wendet einen Patch auf eine Datei an (Search & Replace Modell).",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Pfad zur Datei"},
            "search": {"type": "string", "description": "Zu suchender Text"},
            "replace": {"type": "string", "description": "Ersatztext"},
        },
        "required": ["path", "search", "replace"],
    },
)
def file_patch_tool(path: str = "", search: str = "", replace: str = "", file_path: str = "", filename: str = ""):
    resolved = path or file_path or filename
    if not resolved:
        hint = _workspace_file_hint()
        return {"error": f"Parameter 'path' fehlt.{hint}"}
    resolved = _resolve_workspace_path(resolved)
    ok, err = _check_file_access(resolved, "write")
    if not ok: return {"error": err}

    import os
    if not os.path.exists(resolved):
        return {"error": f"Datei '{resolved}' nicht gefunden."}

    try:
        with open(resolved, "r", encoding="utf-8") as f:
            content = f.read()

        if search not in content:
            return {"error": "Suchtext wurde in der Datei nicht gefunden."}

        new_content = content.replace(search, replace)

        with open(resolved, "w", encoding="utf-8") as f:
            f.write(new_content)

        from agent.common.audit import log_audit
        log_audit("file_patch", {"path": resolved, "search_len": len(search), "replace_len": len(replace)})
        return {"status": "success", "path": resolved}
    except Exception as e:
        return {"error": f"Fehler beim Patchen der Datei: {e}"}
