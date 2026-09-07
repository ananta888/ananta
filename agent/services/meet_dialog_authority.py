"""Re-read ordinary Hub task authority; Worker status is never an authorization."""

import re
import time
from dataclasses import dataclass

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_controls import DialogControls, parse_controls
from ananta_contracts.meet_dialog import OPTIONAL_CONTROL_CAPABILITIES

CAPABILITIES = frozenset(
    {"audio.receive", "chat.read", "chat.send", "avatar.publish", "speech.publish", "screen.publish"}
)


@dataclass(frozen=True)
class DialogAuthority:
    task_id: str
    lease_id: str
    tenant_id: str
    project_id: str
    runtime_id: str
    session_id: str
    room_id: str
    origin: str
    owner_subject: str
    binding_task_id: str
    deadline: int
    capabilities: tuple[str, ...]
    chat_mode: str
    audio_mode: str
    controls: DialogControls
    avatar_selection: dict | None = None
    voice_selection: dict | None = None


class MeetDialogAuthority:
    def __init__(self, tasks, binding, policies, clock=time.time):
        self.tasks, self.binding, self.clock = tasks, binding, clock
        if not isinstance(policies, dict):
            raise ValueError("meet_dialog_policy_invalid")
        self.policies = {}
        for scope, capabilities in policies.items():
            if not isinstance(scope, tuple) or len(scope) != 2 or not all(isinstance(v, str) and v for v in scope):
                raise ValueError("meet_dialog_policy_invalid")
            if not isinstance(capabilities, (tuple, list, set, frozenset)) or not set(capabilities) <= CAPABILITIES:
                raise ValueError("meet_dialog_policy_invalid")
            self.policies[scope] = frozenset(capabilities)

    def current(self, task_id, lease_id, runtime_id):
        from agent.services.source_control_access_policy import HubSourcePrincipal

        task = self.tasks.get_by_id(task_id)
        if (
            task is None
            or task.task_kind != "meet_dialog_session"
            or task.status != "in_progress"
            or getattr(task, "archived", False)
        ):
            raise MeetError("meet_dialog_task_inactive", 403)
        value = (task.worker_execution_context or {}).get("meet_dialog", {})
        fields = {
            "lease_id",
            "runtime_id",
            "session_id",
            "room_id",
            "owner_subject",
            "binding_task_id",
            "deadline",
            "capabilities",
            "chat_mode",
            "audio_mode",
            "audio_job",
            "audio_count",
            "controls",
        }
        if not isinstance(value, dict) or set(value) - {"avatar_selection", "voice_selection"} != fields:
            raise MeetError("meet_dialog_binding_invalid", 403)
        for field in fields - {"deadline", "capabilities", "binding_task_id", "audio_job", "audio_count", "controls"}:
            if not isinstance(value[field], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value[field]):
                raise MeetError("meet_dialog_binding_invalid", 403)
        if (value["lease_id"], value["runtime_id"]) != (lease_id, runtime_id):
            raise MeetError("meet_dialog_runtime_mismatch", 403)
        if type(value["deadline"]) is not int or not self.clock() < value["deadline"] <= self.clock() + 7200:
            raise MeetError("meet_dialog_expired", 403)
        capabilities = value["capabilities"]
        if value["chat_mode"] not in {"off", "mention", "direct_question", "room"}:
            raise MeetError("meet_dialog_policy_denied", 403)
        if (
            value["audio_mode"] not in {"off", "transcribe", "dialog"}
            or type(value["audio_count"]) is not int
            or not 0 <= value["audio_count"] <= 720
            or value["audio_job"] is not None
            and not isinstance(value["audio_job"], dict)
        ):
            raise MeetError("meet_dialog_policy_denied", 403)
        allowed = self.policies.get((task.tenant_id, task.project_id), frozenset())
        if (
            not isinstance(capabilities, list)
            or not capabilities
            or any(not isinstance(item, str) for item in capabilities)
            or len(set(capabilities)) != len(capabilities)
            or not set(capabilities) <= allowed
        ):
            raise MeetError("meet_dialog_policy_denied", 403)
        controls = parse_controls(value["controls"])
        avatar_selection = None
        voice_selection = None
        if "voice_selection" in value:
            from agent.models.meet_voice_selection import parse_voice_selection

            try:
                if "speech.publish" not in capabilities or controls.speech is None:
                    raise ValueError()
                voice_selection = parse_voice_selection(value["voice_selection"], task.tenant_id, task.project_id)
            except ValueError:
                raise MeetError("meet_dialog_voice_selection_invalid", 403) from None
        if "avatar_selection" in value:
            from agent.models.meet_avatar_selection import parse_avatar_selection

            try:
                if "avatar.publish" not in capabilities or controls.avatar is None:
                    raise ValueError()
                avatar_selection = parse_avatar_selection(value["avatar_selection"], task.tenant_id, task.project_id)
            except ValueError:
                raise MeetError("meet_dialog_avatar_selection_invalid", 403) from None
        if any(
            getattr(controls, name) is not None and capability not in capabilities
            for name, capability in OPTIONAL_CONTROL_CAPABILITIES.items()
        ) or (controls.speech is not None and controls.speech.enabled and value["chat_mode"] == "off"):
            raise MeetError("meet_dialog_control_capability_denied", 403)
        parent = value["binding_task_id"]
        if not isinstance(parent, str) or parent and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", parent):
            raise MeetError("meet_dialog_binding_invalid", 403)
        principal = HubSourcePrincipal(value["owner_subject"], task.tenant_id, task.project_id, frozenset({"user"}))
        self.binding.require_write_access(principal, task.project_id, parent)
        stored = self.binding.read(principal, task.project_id, parent)
        if not stored["invite_url"] or self.binding.profile.parse_invite(stored["invite_url"]) != value["room_id"]:
            raise MeetError("meet_dialog_room_changed", 403)
        return DialogAuthority(
            task_id,
            lease_id,
            task.tenant_id,
            task.project_id,
            runtime_id,
            value["session_id"],
            value["room_id"],
            self.binding.profile.origin,
            value["owner_subject"],
            parent,
            value["deadline"],
            tuple(sorted(capabilities)),
            value["chat_mode"],
            value["audio_mode"],
            controls,
            avatar_selection,
            voice_selection,
        )
