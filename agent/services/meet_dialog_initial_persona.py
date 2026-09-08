"""Resolve passive initial pins before Task creation and recheck before dispatch."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from agent.models.meet_avatar_selection import parse_avatar_selection
from agent.models.meet_voice_selection import parse_voice_selection
from agent.models.persona_media import PersonaProfileSelection
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_initial_persona import initial_projection, validate_initial_persona


class InitialProfilePort(Protocol):
    def select(self, principal, project, selection, purpose) -> tuple[dict, dict]: ...
    def require_current(self, principal, project, binding, reference, purpose): ...


@dataclass(frozen=True)
class ResolvedInitialPersona:
    selections: dict
    projection: dict


class MeetDialogInitialPersona:
    def __init__(
        self, images: InitialProfilePort | None, videos: InitialProfilePort | None, voices: InitialProfilePort | None
    ):
        self.images, self.videos, self.voices = images, videos, voices

    def resolve(self, principal, project, value, options):
        try:
            return self._resolve(principal, project, value, options)
        except MeetError:
            raise
        except ValueError:
            # Schema errors can contain caller input or private profile data.
            # Keep the public admission failure bounded and machine-readable.
            raise MeetError("meet_initial_persona_invalid") from None

    def _resolve(self, principal, project, value, options):
        if (
            type(value) is not dict
            or not value
            or set(value) - {"avatar", "voice"}
            or principal.project_id
            and principal.project_id != project
            or set(principal.roles) & {"worker", "service"}
        ):
            raise MeetError("meet_initial_persona_denied", 403)
        selections = {}
        for name, choice in value.items():
            if type(choice) is not dict:
                raise MeetError("meet_initial_persona_invalid")
            mode = choice.get("mode") if name == "avatar" else "persona-voice-v1"
            fields = {"profile", "mode"} if name == "avatar" else {"profile"}
            if mode == "persona-video-v1":
                fields |= {"repeat_mode"}
            if set(choice) != fields or mode not in ("persona-image-v1", "persona-video-v1", "persona-voice-v1"):
                raise MeetError("meet_initial_persona_invalid")
            if (
                name == "avatar"
                and (options.get("avatar_images") is not True or mode == "persona-voice-v1")
                or mode == "persona-video-v1"
                and options.get("avatar_videos") is not True
                or name == "voice"
                and options.get("voice_profiles") is not True
            ):
                raise MeetError("meet_initial_persona_not_negotiated", 403)
            if mode == "persona-video-v1" and choice["repeat_mode"] not in ("loop", "hold_last"):
                raise MeetError("meet_initial_persona_invalid")
            pin = PersonaProfileSelection.model_validate(choice["profile"]).model_dump(mode="json")
            reference, returned = self._port(mode).select(principal, project, pin, "publish")
            if returned != pin:
                raise MeetError("meet_initial_persona_changed", 403)
            selected = {"mode": mode, "reference": reference, "profile": pin}
            if mode == "persona-video-v1":
                selected["repeat_mode"] = choice["repeat_mode"]
            selections[name] = (
                parse_voice_selection(selected, principal.tenant_id, project)
                if name == "voice"
                else parse_avatar_selection(
                    selected, principal.tenant_id, project, videos=options.get("avatar_videos", False)
                )
            )
        projection = validate_initial_persona(
            initial_projection(selections),
            principal.tenant_id,
            project,
            avatar_images=options.get("avatar_images", False),
            avatar_videos=options.get("avatar_videos", False),
            voice_profiles=options.get("voice_profiles", False),
        )
        resolved = ResolvedInitialPersona(deepcopy(selections), deepcopy(projection))
        self.require_current(principal, project, resolved)
        return resolved

    def require_current(self, principal, project, resolved, scope=None):
        if scope is not None and (
            scope.initial_persona != resolved.projection
            or any(getattr(scope, name + "_selection") != selection for name, selection in resolved.selections.items())
        ):
            raise MeetError("meet_initial_persona_changed", 403)
        for selected in resolved.selections.values():
            self._port(selected["mode"]).require_current(
                principal, project, selected["profile"], selected["reference"], "publish"
            )

    def _port(self, mode):
        port = {"persona-image-v1": self.images, "persona-video-v1": self.videos, "persona-voice-v1": self.voices}[mode]
        if port is None:
            raise MeetError("meet_initial_persona_provider_unavailable", 409)
        return port
