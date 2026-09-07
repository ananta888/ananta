"""Explicit visual-selection strategies, separate from Hub task orchestration."""

import re
from dataclasses import dataclass

from agent.services.meet_contract import MeetError


@dataclass(frozen=True)
class VisualStrategy:
    selector: str
    media_field: str
    assets: object
    profiles: object = None
    profile_task_field: str | None = None
    repeat_required: bool = False

    def require_available(self):
        if self.assets is None or self.profile_task_field is not None and self.profiles is None:
            raise MeetError(f"meet_{self.selector.removesuffix('_id')}_unavailable", 403)

    def require_current(self, principal, project, reference, purpose, binding):
        self.require_available()
        if self.profile_task_field is not None:
            self.profiles.require_current(principal, project, binding, reference)
        self.assets.require_current(principal, project, reference, purpose)

    def prepare(self, principal, project, payload, purpose):
        self.require_available()
        value = payload[self.selector]
        kwargs = {"repeat_mode": payload["video_repeat_mode"]} if self.repeat_required else {}
        if self.profile_task_field is not None:
            assignment, binding = self.profiles.prepare(principal, project, value, purpose, **kwargs)
        else:
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
                raise MeetError("meet_" + self.media_field + "_unavailable", 403)
            assignment, binding = self.assets.prepare(principal, project, value, purpose, **kwargs), None
        return BoundVisual(self, principal, project, purpose, assignment, binding)


@dataclass(frozen=True)
class BoundVisual:
    strategy: VisualStrategy
    principal: object
    project: str
    purpose: str
    assignment: dict
    binding: dict | None

    @property
    def worker_fields(self):
        return {self.strategy.media_field: self.assignment}

    @property
    def hub_fields(self):
        return {self.strategy.profile_task_field: self.binding} if self.strategy.profile_task_field else {}

    def require_current(self):
        self.strategy.require_current(
            self.principal, self.project, self.assignment["reference"], self.purpose, self.binding
        )

    def require_result(self, result):
        if result.get(self.strategy.media_field) != self.assignment["reference"] or {
            "persona_image",
            "persona_video",
        } & set(result) != {self.strategy.media_field}:
            raise MeetError("meet_persona_result_mismatch", 409)


class MeetVisualSelections:
    def __init__(self, *, images, image_profiles, videos, video_profiles=None):
        self.strategies = (
            VisualStrategy("persona_image_id", "persona_image", images),
            VisualStrategy("persona_profile", "persona_image", images, image_profiles, "hub_persona_profile"),
            VisualStrategy("persona_video_id", "persona_video", videos, repeat_required=True),
            VisualStrategy(
                "persona_video_profile", "persona_video", videos, video_profiles, "hub_persona_video_profile", True
            ),
        )

    def validate_payload(self, payload):
        selectors = {strategy.selector for strategy in self.strategies}
        if (
            not isinstance(payload, dict)
            or set(payload) - selectors - {"publish_to_meet", "video_repeat_mode"} != {"text"}
            or type(payload.get("publish_to_meet", False)) is not bool
            or len(selectors & set(payload)) > 1
        ):
            raise MeetError("meet_turn_payload_invalid")
        repeated = any(s.repeat_required and s.selector in payload for s in self.strategies)
        if repeated != ("video_repeat_mode" in payload) or (
            repeated and payload["video_repeat_mode"] not in ("loop", "hold_last")
        ):
            raise MeetError("meet_turn_payload_invalid")

    def prepare(self, principal, project, payload):
        self.validate_payload(payload)
        purpose = "publish" if payload.get("publish_to_meet") else "preview"
        for strategy in self.strategies:
            if strategy.selector in payload:
                return strategy.prepare(principal, project, payload, purpose)
        return None

    def require_context(self, principal, project, context):
        media = {s.media_field for s in self.strategies} & set(context)
        profiles = {s.profile_task_field.removeprefix("hub_") for s in self.strategies if s.profile_task_field} & set(
            context
        )
        if not media and not profiles:
            return
        if len(media) != 1 or len(profiles) > 1:
            raise MeetError("meet_persona_context_invalid", 409)
        candidates = [
            s
            for s in self.strategies
            if s.media_field in media
            and (s.profile_task_field.removeprefix("hub_") if s.profile_task_field else None)
            == next(iter(profiles), None)
        ]
        if len(candidates) != 1:
            raise MeetError("meet_persona_context_invalid", 409)
        strategy = candidates[0]
        if strategy.repeat_required and context.get("persona_video_repeat_mode") not in ("loop", "hold_last"):
            raise MeetError("meet_persona_context_invalid", 409)
        binding = context[strategy.profile_task_field.removeprefix("hub_")] if strategy.profile_task_field else None
        strategy.require_current(
            principal, project, context[strategy.media_field], context.get("persona_purpose"), binding
        )
