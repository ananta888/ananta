"""Validate registered own publications; this never asserts media delivery."""

import re

from agent.models.meet_membership import validate_membership
from agent.services.meet_contract import MeetError

SCHEMA = "ananta.meet-session-observation.v1"
SOURCE_RIGHTS = {"screen": "screen.publish", "camera": "avatar.publish", "microphone": "speech.publish"}


def validate_observation(value, scope, issuer, session_id, nonce, now_ms):
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "nonce",
        "lease",
        "binding",
        "peerId",
        "roomId",
        "membershipEpoch",
        "publicationRevision",
        "publications",
    }:
        raise MeetError("meet_observation_contract_invalid", 502)
    validate_membership(value, scope, issuer, session_id, nonce, now_ms, schema=SCHEMA)
    revision, publications = value["publicationRevision"], value["publications"]
    if (
        type(revision) is not int
        or not 0 <= revision < 2**53
        or not isinstance(publications, list)
        or len(publications) > len(SOURCE_RIGHTS)
    ):
        raise MeetError("meet_observation_publications_invalid", 502)
    ids, sources, epochs = set(), set(), set()
    for item in publications:
        if (
            not isinstance(item, dict)
            or set(item) != {"publicationId", "source", "publicationEpoch"}
            or not isinstance(item["publicationId"], str)
            or not re.fullmatch(r"[A-Za-z0-9_={}:-]{1,128}", item["publicationId"])
            or item["publicationId"] in ids
            or not isinstance(item["source"], str)
            or item["source"] not in SOURCE_RIGHTS
            or SOURCE_RIGHTS[item["source"]] not in scope.capabilities
            or item["source"] in sources
            or type(item["publicationEpoch"]) is not int
            or not 1 <= item["publicationEpoch"] <= revision
            or item["publicationEpoch"] in epochs
        ):
            raise MeetError("meet_observation_publications_invalid", 502)
        ids.add(item["publicationId"])
        sources.add(item["source"])
        epochs.add(item["publicationEpoch"])
    return value
