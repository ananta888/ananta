"""Explicit operator opt-in; absent credentials leave the directory untouched."""

import os

from agent.services.meet_contract import MeetError


def configure_meet_public_room(app, environ=None):
    """Wire the public-room publication, or stay disabled without config."""
    from agent.bootstrap.meet_room_allocation import allocation_scopes
    from agent.services.meet_public_room_service import MeetPublicRoomService, load_config

    environ = os.environ if environ is None else environ
    try:
        config = load_config(environ)
    except MeetError as error:
        raise ValueError(error.code) from None
    if config is None:
        return
    if app.config.get("ROLE") != "hub" or "meet_binding_service" not in app.extensions:
        raise ValueError("meet_public_room_hub_required")
    binding = app.extensions["meet_binding_service"]
    if config.server_url != binding.profile.origin:
        # The companion joins the invite the binding carries; a directory on a
        # different origin would hand out a link this Hub cannot mint.
        raise ValueError("meet_public_room_origin_mismatch")

    from agent.database import engine
    from agent.repositories.meet_public_rooms import SqlPublicRoomStore
    from agent.services.meet_public_room_binding import MeetPublicRoomPublication

    memory = SqlPublicRoomStore(engine)
    memory.initialize()
    key = "ANANTA_MEET_PUBLIC_ROOM_SCOPES"
    raw = app.config.get(key, environ.get(key, ""))
    try:
        scopes = allocation_scopes(raw) if raw else None
    except ValueError:
        raise ValueError("meet_public_room_policy_invalid") from None
    app.extensions["meet_public_rooms"] = MeetPublicRoomService(config, memory)
    app.extensions["meet_public_room_publication"] = MeetPublicRoomPublication(
        binding, app.extensions["meet_public_rooms"], allowed_scopes=scopes
    )
