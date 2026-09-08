"""Fixed installed dialog-handler semantics, never caller-selected capture rights."""

from dataclasses import dataclass

CAPABILITIES = frozenset(
    {"audio.receive", "chat.read", "chat.send", "avatar.publish", "speech.publish", "screen.publish"}
)
SOURCE_CLASSES = frozenset(
    {"agent_browser", "generated_audio", "generated_video", "persona_image", "persona_video", "human_device_capture"}
)
DENIED_OPERATIONS = ("human_device_capture", "record", "model.train", "tool.execute")


@dataclass(frozen=True)
class DialogSourceProfile:
    """Upper bounds, not current controls, asset admission or provenance evidence."""

    capabilities: tuple[str, ...]
    publications: tuple[tuple[str, tuple[str, ...]], ...]

    def projection(self):
        # Fresh containers: a caller cannot mutate this immutable policy value.
        return {
            "schema": "ananta.meet-source-profile.v1",
            "execution_profile": "meet-owned-dialog-v1",
            "capabilities": list(self.capabilities),
            "publication_sources": {channel: list(classes) for channel, classes in self.publications},
            "denied_operations": list(DENIED_OPERATIONS),
        }

    def require_projection(self, value):
        if not isinstance(value, dict) or value != self.projection():
            raise ValueError("meet_source_profile_mismatch")


def dialog_source_profile(capabilities, *, avatar_images=False):
    """Compile only the existing v1 handler; no arbitrary profile/plugin registry."""
    if (
        not isinstance(capabilities, (list, tuple))
        or not capabilities
        or any(not isinstance(capability, str) for capability in capabilities)
        or len(capabilities) != len(set(capabilities))
        or not set(capabilities) <= CAPABILITIES
        or type(avatar_images) is not bool
        or avatar_images
        and "avatar.publish" not in capabilities
    ):
        raise ValueError("meet_source_profile_invalid")
    publications = []
    if "screen.publish" in capabilities:
        publications.append(("screen", ("agent_browser",)))
    if "speech.publish" in capabilities:
        publications.append(("speech", ("generated_audio",)))
    if "avatar.publish" in capabilities:
        classes = ("generated_video", "persona_image") if avatar_images else ("generated_video",)
        publications.append(("avatar", classes))
    return DialogSourceProfile(tuple(sorted(capabilities)), tuple(publications))
