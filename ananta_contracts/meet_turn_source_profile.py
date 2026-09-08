"""Fixed installed one-shot renderer bounds, not provenance or admission rights."""

from dataclasses import dataclass

from ananta_contracts.meet_source_profile import DENIED_OPERATIONS


@dataclass(frozen=True)
class TurnSourceProfile:
    visual_input: str
    publishes: bool

    def __post_init__(self):
        if (
            not isinstance(self.visual_input, str)
            or self.visual_input not in {"none", "persona_image", "persona_video"}
            or type(self.publishes) is not bool
        ):
            raise ValueError("meet_turn_source_profile_invalid")

    def projection(self):
        video = ["generated_video"] + ([] if self.visual_input == "none" else [self.visual_input])
        return {
            "schema": "ananta.meet-turn-source-profile.v1",
            "execution_profile": "meet-local-render-v1",
            "input_sources": [] if self.visual_input == "none" else [self.visual_input],
            "generated_sources": {"speech": ["generated_audio"], "avatar": list(video)},
            "publication_sources": {"speech": ["generated_audio"], "avatar": list(video)} if self.publishes else {},
            "capabilities": ["avatar.publish", "chat.send", "speech.publish"] if self.publishes else [],
            "denied_operations": [*DENIED_OPERATIONS, "audio.receive", "chat.read", "screen.publish"],
        }

    def require_projection(self, value):
        if not isinstance(value, dict) or value != self.projection():
            raise ValueError("meet_turn_source_profile_mismatch")


def turn_source_profile(*, persona_image=False, persona_video=False, publish=False):
    if any(type(value) is not bool for value in (persona_image, persona_video, publish)) or (
        persona_image and persona_video
    ):
        raise ValueError("meet_turn_source_profile_invalid")
    visual = "persona_image" if persona_image else "persona_video" if persona_video else "none"
    return TurnSourceProfile(visual, publish)


def profile_for_turn(turn):
    # The signed turn remains validated by its existing closed contract. These
    # fields describe installed code; they are never selectable wire extensions.
    forbidden = {"source_profile", "source_class", "execution_profile", "human_device_capture"}
    if not isinstance(turn, dict) or forbidden & set(turn):
        raise ValueError("meet_turn_source_profile_invalid")
    return turn_source_profile(
        persona_image="persona_image" in turn, persona_video="persona_video" in turn, publish="meeting" in turn
    )
