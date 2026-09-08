"""Closed Hub-only phase metadata; execution authority remains the Task aggregate."""

import hashlib
import json
import re

from agent.services.meet_contract import MeetError

SCHEMA = "ananta.meet-dialog-phase-record.v1"
TERMINAL = frozenset({"completed", "failed", "cancelled"})
TRANSITIONS = {
    "queued": {"admitted", "stopping"},
    "admitted": {"connecting", "stopping"},
    "connecting": {"joined", "stopping"},
    "joined": {"publishing", "stopping"},
    "publishing": {"joined", "stopping"},
    "stopping": set(),
}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def phase_binding(task_id, tenant, project, context):
    keys = (
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
    )
    try:
        return _digest(
            [
                task_id,
                tenant,
                project,
                {key: context[key] for key in keys},
                "avatar_selection" in context,
                "voice_selection" in context,
            ]
        )
    except (KeyError, TypeError, ValueError):
        raise MeetError("meet_dialog_phase_binding_invalid", 409) from None


def timestamp(value):
    if type(value) is not int or not 0 <= value < 2**53:
        raise MeetError("meet_dialog_phase_clock_invalid", 409)
    return value


def queued(binding, now):
    return validate_record(
        {
            "schema": SCHEMA,
            "binding": binding,
            "phase": "queued",
            "revision": 1,
            "since": timestamp(now),
            "membership": None,
            "observation": None,
        },
        binding,
    )


def validate_record(value, binding):
    if value is None:
        raise MeetError("meet_dialog_phase_unavailable", 409)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "binding", "phase", "revision", "since", "membership", "observation"}
        or value["schema"] != SCHEMA
        or value["binding"] != binding
        or not isinstance(binding, str)
        or not re.fullmatch(r"[a-f0-9]{64}", binding)
        or not isinstance(value["phase"], str)
        or value["phase"] not in TRANSITIONS
        or type(value["revision"]) is not int
        or not 1 <= value["revision"] < 2**53 - 1
    ):
        raise MeetError("meet_dialog_phase_record_invalid", 409)
    timestamp(value["since"])
    membership = value["membership"]
    if membership is not None and (
        not isinstance(membership, dict)
        or set(membership) != {"session_id", "peer_id"}
        or not isinstance(membership["session_id"], str)
        or not re.fullmatch(r"ms_[A-Za-z0-9_-]{32}", membership["session_id"])
        or not isinstance(membership["peer_id"], str)
        or not re.fullmatch(r"[a-f0-9]{16}", membership["peer_id"])
    ):
        raise MeetError("meet_dialog_phase_membership_invalid", 409)
    observation = value["observation"]
    if observation is not None:
        if (
            not isinstance(observation, dict)
            or set(observation) != {"revision", "digest", "sources", "observed_at", "valid_until"}
            or type(observation["revision"]) is not int
            or not 0 <= observation["revision"] < 2**53
            or not isinstance(observation["digest"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", observation["digest"])
            or not isinstance(observation["sources"], list)
            or any(
                not isinstance(s, str) or s not in {"screen", "camera", "microphone"} for s in observation["sources"]
            )
            or observation["sources"] != sorted(set(observation["sources"]))
        ):
            raise MeetError("meet_dialog_phase_observation_invalid", 409)
        if not timestamp(observation["observed_at"]) < timestamp(observation["valid_until"]):
            raise MeetError("meet_dialog_phase_observation_invalid", 409)
    if (
        (value["phase"] in {"queued", "admitted", "connecting"} and (membership is not None or observation is not None))
        or value["phase"] in {"joined", "publishing"}
        and membership is None
        or observation is not None
        and membership is None
        or value["phase"] == "publishing"
        and (observation is None or not observation["sources"])
        or value["phase"] == "joined"
        and observation is not None
        and observation["sources"]
    ):
        raise MeetError("meet_dialog_phase_record_invalid", 409)
    return value


def transition(record, target, now, *, membership=None, observation=None):
    validate_record(record, record["binding"])
    timestamp(now)
    if now < record["since"]:
        raise MeetError("meet_dialog_phase_clock_invalid", 409)
    if target != record["phase"] and target not in TRANSITIONS[record["phase"]]:
        raise MeetError("meet_dialog_phase_transition_invalid", 409)
    changed = record | {"phase": target}
    if membership is not None:
        if record["membership"] is not None and membership != record["membership"]:
            raise MeetError("meet_dialog_phase_membership_changed", 409)
        changed["membership"] = dict(membership)
    if observation is not None:
        previous = record["observation"]
        if previous is not None and (
            observation["observed_at"] < previous["observed_at"]
            or observation["revision"] < previous["revision"]
            or observation["revision"] == previous["revision"]
            and observation["digest"] != previous["digest"]
        ):
            raise MeetError("meet_dialog_phase_observation_stale", 409)
        changed["observation"] = observation
    if changed == record:
        return record
    if record["revision"] >= 2**53 - 2:
        raise MeetError("meet_dialog_phase_revision_exhausted", 409)
    changed.update(revision=record["revision"] + 1, since=now)
    return validate_record(changed, record["binding"])


def publication_observation(state, now, deadline):
    """Input is the separate, already validated Meet observation transport result."""
    return {
        "revision": state["publicationRevision"],
        "digest": _digest(sorted(state["publications"], key=lambda item: item["publicationId"])),
        "sources": sorted(item["source"] for item in state["publications"]),
        "observed_at": timestamp(now),
        "valid_until": min(state["lease"]["expiresAt"], deadline * 1000),
    }


def projection(task_id, task_status, record, now):
    validate_record(record, record["binding"])
    timestamp(now)
    if task_status not in TERMINAL | {"in_progress"}:
        raise MeetError("meet_dialog_phase_task_inactive", 409)
    terminal = task_status in TERMINAL
    observation = record["observation"]
    fresh = (
        not terminal
        and record["phase"] != "stopping"
        and observation is not None
        and 0 <= now - observation["observed_at"] < 5000
        and now < observation["valid_until"]
    )
    return {
        "schema": "ananta.meet-dialog-phase.v1",
        "task_id": task_id,
        "phase": task_status if terminal else record["phase"],
        "revision": record["revision"] + int(terminal),
        "task_status": task_status,
        "last_phase_at": record["since"],
        "observation_fresh": bool(fresh),
        "observed_at": observation["observed_at"] if observation else None,
        "publication_revision": observation["revision"] if observation else None,
        "registered_sources": list(observation["sources"]) if observation else [],
    }
