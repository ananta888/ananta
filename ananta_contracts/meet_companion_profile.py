"""Closed additive Companion execution negotiation; no caller-selected command."""


def validate_companion_profile(value):
    if "companion_execution" not in value:
        return False
    if (value["companion_execution"] is not True
            or not {"chat.read", "chat.send"} <= set(value.get("capabilities", ()))
            or not set(value.get("capabilities", ())) <= {"chat.read", "chat.send", "avatar.publish", "speech.publish"}
            or value.get("audio_mode", "off") != "off"
            or any(field in value for field in (
                "avatar_images", "avatar_videos", "voice_profiles", "initial_persona", "browser_workspace",
                "audio_profile", "speaker_floor", "reconnect", "media_timing", "avatar_selection", "voice_selection",
            ))):
        raise ValueError("meet_companion_execution_profile_invalid")
    return True
