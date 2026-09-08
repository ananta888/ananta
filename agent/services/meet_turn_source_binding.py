"""Hub-only persistence checks for the installed one-shot source projection."""

from ananta_contracts.meet_turn_source_profile import profile_for_turn, turn_source_profile


def source_metadata(turn):
    return {"publication_requested": "meeting" in turn, "source_profile": profile_for_turn(turn).projection()}


def require_source_context(context, turn=None):
    if not isinstance(context, dict):
        raise ValueError("meet_turn_source_profile_mismatch")
    if not {"source_profile", "publication_requested"} & set(context):
        return  # Legacy tasks retain only the original fixed renderer semantics.
    if not {"source_profile", "publication_requested"} <= set(context):
        raise ValueError("meet_turn_source_profile_mismatch")
    profile = turn_source_profile(
        persona_image="persona_image" in context,
        persona_video="persona_video" in context,
        publish=context["publication_requested"],
    )
    profile.require_projection(context["source_profile"])
    if turn is not None:
        expected = source_metadata(turn)
        if any(context[key] != value for key, value in expected.items()):
            raise ValueError("meet_turn_source_profile_mismatch")


def source_context_matches(context, turn):
    try:
        require_source_context(context, turn)
        return True
    except ValueError:
        return False
