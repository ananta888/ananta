from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.services.live_terminal_session_service import LiveTerminalSessionService

LOGGER = logging.getLogger("agent.embedded_tool_session")

# Session type constants
SESSION_TYPE_EDITOR = "embedded_editor"
SESSION_TYPE_TOOL = "embedded_tool"

# Target type constants
TARGET_WORKER = "worker"
TARGET_HUB = "hub"
TARGET_HUB_AS_WORKER = "hub_as_worker"
_VALID_TARGETS = {TARGET_WORKER, TARGET_HUB, TARGET_HUB_AS_WORKER}

# Result reason codes
REASON_OK = "ok"
REASON_PERMISSION_DENIED = "permission_denied"
REASON_TOOL_NOT_ALLOWED = "tool_not_allowed"
REASON_PATH_INVALID = "path_invalid"
REASON_UNKNOWN_TOOL = "unknown_tool"
REASON_INVALID_TARGET = "invalid_target"
REASON_LAUNCH_FAILED = "launch_failed"


@dataclass
class TuiToolPolicy:
    hub_tools_enabled: bool = False
    worker_tools_enabled: bool = True
    hub_as_worker_tools_enabled: bool = False
    allow_write_editor: bool = True
    allow_readonly_editor: bool = True
    allow_custom_editor_command: bool = False

    def is_target_allowed(self, target_type: str) -> bool:
        if target_type == TARGET_WORKER:
            return self.worker_tools_enabled
        if target_type == TARGET_HUB:
            return self.hub_tools_enabled
        if target_type == TARGET_HUB_AS_WORKER:
            return self.hub_as_worker_tools_enabled
        return False


@dataclass
class EmbeddedSessionMeta:
    session_id: str
    session_type: str
    target_type: str
    workspace: str
    file_path: str
    editor_id: str
    readonly: bool
    tool_id: str
    launched_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "session_type": self.session_type,
            "target_type": self.target_type,
            "workspace": self.workspace,
            "file_path": self.file_path,
            "editor_id": self.editor_id,
            "readonly": self.readonly,
            "tool_id": self.tool_id,
            "launched_at": self.launched_at,
        }


@dataclass(frozen=True)
class EmbeddedSessionResult:
    ok: bool
    session_id: str
    reason: str
    meta: EmbeddedSessionMeta | None = None

    @classmethod
    def success(cls, session_id: str, meta: EmbeddedSessionMeta) -> "EmbeddedSessionResult":
        return cls(ok=True, session_id=session_id, reason=REASON_OK, meta=meta)

    @classmethod
    def failure(cls, session_id: str, reason: str) -> "EmbeddedSessionResult":
        return cls(ok=False, session_id=session_id, reason=reason, meta=None)


class EmbeddedToolSessionService:
    """Orchestrates launching editors and TUI tools inside LiveTerminalSessionService sessions.

    Responsibilities:
    - Validate target type and policy before launch.
    - Validate file paths via WorkspacePathValidator for editor sessions.
    - Resolve editor commands via EditorResolver.
    - Verify tool commands via TuiToolRegistry allowlist.
    - Launch via ManagedLiveTerminalSession.run_foreground_command(argv).
    - Track session metadata (session_type, target_type, workspace, file, editor, tool).
    """

    def __init__(
        self,
        *,
        session_service: "LiveTerminalSessionService | None" = None,
        registry=None,
        resolver=None,
    ) -> None:
        self._session_service = session_service
        self._registry = registry
        self._resolver = resolver
        self._lock = threading.Lock()
        self._metadata: dict[str, EmbeddedSessionMeta] = {}

    def _get_session_service(self) -> "LiveTerminalSessionService":
        if self._session_service is not None:
            return self._session_service
        from agent.services.live_terminal_session_service import live_terminal_session_service
        return live_terminal_session_service

    def _get_registry(self):
        if self._registry is not None:
            return self._registry
        from agent.services.tui_tool_registry import get_tui_tool_registry
        return get_tui_tool_registry()

    def _get_resolver(self):
        if self._resolver is not None:
            return self._resolver
        from agent.services.editor_resolver import get_editor_resolver
        return get_editor_resolver()

    def _store_meta(self, meta: EmbeddedSessionMeta) -> None:
        with self._lock:
            self._metadata[meta.session_id] = meta

    def _remove_meta(self, session_id: str) -> None:
        with self._lock:
            self._metadata.pop(session_id, None)

    def launch_editor(
        self,
        session_id: str,
        file_path: str,
        *,
        workspace: str,
        target_type: str = TARGET_WORKER,
        with_editor: str | None = None,
        readonly: bool = False,
        policy: TuiToolPolicy | None = None,
    ) -> EmbeddedSessionResult:
        """Open a file in the resolved editor inside a controlled terminal session."""
        sid = str(session_id or "").strip()
        effective_policy = policy or TuiToolPolicy()

        if target_type not in _VALID_TARGETS:
            return EmbeddedSessionResult.failure(sid, REASON_INVALID_TARGET)

        if not effective_policy.is_target_allowed(target_type):
            LOGGER.warning("Editor launch denied: target_type=%s policy=%s", target_type, effective_policy)
            return EmbeddedSessionResult.failure(sid, REASON_PERMISSION_DENIED)

        if readonly and not effective_policy.allow_readonly_editor:
            return EmbeddedSessionResult.failure(sid, REASON_PERMISSION_DENIED)
        if not readonly and not effective_policy.allow_write_editor:
            return EmbeddedSessionResult.failure(sid, REASON_PERMISSION_DENIED)

        from agent.services.workspace_path_validator import WorkspacePathValidator
        validator = WorkspacePathValidator(workspace)
        path_result = validator.validate(file_path)
        if not path_result.ok:
            LOGGER.warning("Editor launch blocked: path=%r reason=%s", file_path, path_result.reason)
            return EmbeddedSessionResult.failure(sid, REASON_PATH_INVALID)

        resolver = self._get_resolver()
        resolution = resolver.resolve(path_result.resolved_path, with_editor=with_editor)
        argv = resolution.build_argv(path_result.resolved_path, readonly=readonly)

        try:
            svc = self._get_session_service()
            session = svc.ensure_session(sid)
            session.run_foreground_command(argv, timeout=3600, cwd=workspace, reset_output=True)
        except Exception:
            LOGGER.exception("Editor launch failed for session %s", sid)
            return EmbeddedSessionResult.failure(sid, REASON_LAUNCH_FAILED)

        meta = EmbeddedSessionMeta(
            session_id=sid,
            session_type=SESSION_TYPE_EDITOR,
            target_type=target_type,
            workspace=workspace,
            file_path=path_result.resolved_path,
            editor_id=resolution.editor_id,
            readonly=readonly,
            tool_id="",
        )
        self._store_meta(meta)
        LOGGER.info(
            "Editor session launched: session=%s editor=%s file=%s target=%s readonly=%s",
            sid, resolution.editor_id, path_result.resolved_path, target_type, readonly,
        )
        return EmbeddedSessionResult.success(sid, meta)

    def launch_tool(
        self,
        session_id: str,
        tool_id: str,
        *,
        workspace: str,
        target_type: str = TARGET_WORKER,
        policy: TuiToolPolicy | None = None,
    ) -> EmbeddedSessionResult:
        """Launch a TUI tool (lazygit, ranger, …) inside a controlled terminal session."""
        sid = str(session_id or "").strip()
        effective_policy = policy or TuiToolPolicy()

        if target_type not in _VALID_TARGETS:
            return EmbeddedSessionResult.failure(sid, REASON_INVALID_TARGET)

        if not effective_policy.is_target_allowed(target_type):
            LOGGER.warning("Tool launch denied: tool=%s target=%s", tool_id, target_type)
            return EmbeddedSessionResult.failure(sid, REASON_PERMISSION_DENIED)

        registry = self._get_registry()
        tool_profile = registry.get_tool_profile(tool_id)
        if tool_profile is None:
            return EmbeddedSessionResult.failure(sid, REASON_UNKNOWN_TOOL)

        if not registry.is_allowed_tool(tool_profile.command):
            return EmbeddedSessionResult.failure(sid, REASON_TOOL_NOT_ALLOWED)

        workdir = tool_profile.working_directory_template.replace("{workspace}", workspace)
        argv_args = [a.replace("{workspace}", workspace) for a in tool_profile.args_template]
        argv = [tool_profile.command] + argv_args

        try:
            svc = self._get_session_service()
            session = svc.ensure_session(sid)
            session.run_foreground_command(argv, timeout=3600, cwd=workdir, reset_output=True)
        except Exception:
            LOGGER.exception("Tool launch failed for session %s tool %s", sid, tool_id)
            return EmbeddedSessionResult.failure(sid, REASON_LAUNCH_FAILED)

        meta = EmbeddedSessionMeta(
            session_id=sid,
            session_type=SESSION_TYPE_TOOL,
            target_type=target_type,
            workspace=workspace,
            file_path="",
            editor_id="",
            readonly=False,
            tool_id=tool_id,
        )
        self._store_meta(meta)
        LOGGER.info("Tool session launched: session=%s tool=%s target=%s", sid, tool_id, target_type)
        return EmbeddedSessionResult.success(sid, meta)

    def get_session_meta(self, session_id: str) -> EmbeddedSessionMeta | None:
        with self._lock:
            return self._metadata.get(str(session_id or "").strip())

    def close_embedded_session(self, session_id: str) -> None:
        sid = str(session_id or "").strip()
        self._remove_meta(sid)
        try:
            self._get_session_service().close_session(sid)
        except Exception:
            LOGGER.debug("Failed to close embedded session %s", sid, exc_info=True)

    def list_embedded_sessions(self) -> list[EmbeddedSessionMeta]:
        with self._lock:
            return list(self._metadata.values())


_service: EmbeddedToolSessionService | None = None
_service_lock = threading.Lock()


def get_embedded_tool_session_service() -> EmbeddedToolSessionService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = EmbeddedToolSessionService()
    return _service
