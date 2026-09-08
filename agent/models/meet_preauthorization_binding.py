"""Immutable original dispatch binding; mutable source selections stay separate."""

import re
from copy import deepcopy

from agent.models.meet_preauthorization_policy import SCOPE_FIELDS, digest, identifier, integer
from agent.services.meet_contract import MeetError, MeetProfile
from ananta_contracts.meet_initial_persona import validate_initial_persona
from ananta_contracts.meet_source_profile import dialog_source_profile


def assignment_projection(task_id, tenant, project, origin, context):
    if type(context) is not dict:
        raise MeetError("meet_preauthorization_binding_invalid", 403)
    fields = {
        "task_id": task_id,
        "tenant_id": tenant,
        "project_id": project,
        "origin": origin,
        "parent_task_id": context.get("binding_task_id"),
        "owner_subject": context.get("owner_subject"),
        **{k: context.get(k) for k in ("lease_id", "runtime_id", "session_id", "room_id")},
    }
    for key, value in fields.items():
        if key != "origin":
            identifier(value)
    if type(origin) is not str:
        raise MeetError("meet_preauthorization_binding_invalid", 403)
    MeetProfile(origin).invite(fields["room_id"])
    deadline = integer(context.get("deadline"), maximum=2**42)
    caps = context.get("capabilities")
    if type(caps) is not list or any(type(cap) is not str for cap in caps) or len(caps) != len(set(caps)):
        raise MeetError("meet_preauthorization_binding_invalid", 403)
    dialog_source_profile(
        caps, avatar_images="avatar_selection" in context, avatar_videos=context.get("avatar_videos", False)
    )
    if "avatar_videos" in context and context["avatar_videos"] is not True:
        raise MeetError("meet_preauthorization_binding_invalid", 403)
    initial = {}
    if "initial_persona" in context:
        try:
            initial["initial_persona"] = deepcopy(
                validate_initial_persona(
                    context["initial_persona"],
                    tenant,
                    project,
                    avatar_images="avatar_selection" in context,
                    avatar_videos=context.get("avatar_videos", False),
                    voice_profiles="voice_selection" in context,
                )
            )
        except ValueError:
            raise MeetError("meet_preauthorization_binding_invalid", 403) from None
    chat, audio = context.get("chat_mode"), context.get("audio_mode")
    if type(chat) is not str or chat not in {"off", "mention", "direct_question", "room"}:
        raise MeetError("meet_preauthorization_binding_invalid", 403)
    if type(audio) is not str or audio not in {"off", "transcribe", "dialog"}:
        raise MeetError("meet_preauthorization_binding_invalid", 403)
    return (
        fields
        | initial
        | ({"avatar_videos": True} if context.get("avatar_videos") is True else {})
        | {
            "schema": "ananta.meet-preauthorized-assignment.v1",
            "deadline": deadline,
            "capabilities": sorted(caps),
            "chat_mode": chat,
            "audio_mode": audio,
            "avatar_images": "avatar_selection" in context,
            "voice_profiles": "voice_selection" in context,
        }
    )


def scope_key(assignment):
    return digest({key: assignment[key] for key in SCOPE_FIELDS})


def policy_binding(policy_id, revision, assignment_digest):
    identifier(policy_id)
    if policy_id.startswith(("SRC_", "RUN_")):
        raise MeetError("meet_preauthorization_binding_invalid", 403)
    integer(revision)
    if type(assignment_digest) is not str or not re.fullmatch(r"[a-f0-9]{64}", assignment_digest):
        raise MeetError("meet_preauthorization_binding_invalid", 403)
    return {
        "schema": "ananta.meet-preauthorization-binding.v1",
        "policy_id": policy_id,
        "revision": revision,
        "assignment_digest": assignment_digest,
    }


def validate_policy_binding(value):
    if type(value) is not dict or set(value) != {"schema", "policy_id", "revision", "assignment_digest"}:
        raise MeetError("meet_preauthorization_binding_invalid", 403)
    if value != policy_binding(value["policy_id"], value["revision"], value["assignment_digest"]):
        raise MeetError("meet_preauthorization_binding_invalid", 403)
    return dict(value)
