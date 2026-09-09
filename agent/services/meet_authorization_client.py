"""TLS backchannel to Meet's current control-plane state, not Worker assertions."""

import re
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import replace

from agent.models.meet_membership import validate_membership
from agent.services.meet_contract import MeetError
from ananta_contracts.http_read_failure import transient_http_read_error
from ananta_contracts.meet_receive_capability import receive_capability
from worker.meet_media.contract import encode
from worker.meet_media.persona_http import read_bounded


def validate_authorization(value, scope, issuer, session_id, nonce, now_ms):
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "nonce",
        "lease",
        "binding",
        "peerId",
        "roomId",
        "membershipEpoch",
        "receiveRevision",
        "grants",
        "publications",
    }:
        raise MeetError("meet_authorization_contract_invalid", 502)
    validate_membership(value, scope, issuer, session_id, nonce, now_ms, schema="ananta.meet-authorization.v1")
    if (
        type(value["receiveRevision"]) is not int
        or not 0 <= value["receiveRevision"] < 2**53
        or not isinstance(value["grants"], list)
        or len(value["grants"]) > 19
    ):
        raise MeetError("meet_authorization_state_invalid", 502)
    seen = set()
    for grant in value["grants"]:
        if (
            not isinstance(grant, dict)
            or set(grant) != {"publisherPeerId", "machinePeerId", "publicationIds", "chatRead", "expiresAt"}
            or not isinstance(grant["publisherPeerId"], str)
            or not re.fullmatch(r"[a-f0-9]{16}", grant["publisherPeerId"])
            or grant["publisherPeerId"] == value["peerId"]
            or grant["publisherPeerId"] in seen
            or grant["machinePeerId"] != value["peerId"]
            or type(grant["chatRead"]) is not bool
            or type(grant["expiresAt"]) is not int
            or not now_ms < grant["expiresAt"] <= now_ms + 600_000
            or not isinstance(grant["publicationIds"], list)
            or len(grant["publicationIds"]) > 4
            or any(
                not isinstance(p, str) or not re.fullmatch(r"[A-Za-z0-9_={}:-]{1,128}", p)
                for p in grant["publicationIds"]
            )
            or len(set(grant["publicationIds"])) != len(grant["publicationIds"])
        ):
            raise MeetError("meet_authorization_grant_invalid", 502)
        seen.add(grant["publisherPeerId"])
    publications = value["publications"]
    if not isinstance(publications, list) or len(publications) > 76:
        raise MeetError("meet_authorization_publications_invalid", 502)
    seen = set()
    for publication in publications:
        if (
            not isinstance(publication, dict)
            or set(publication) != {"peerId", "publicationId", "source", "publicationEpoch"}
            or not isinstance(publication["peerId"], str)
            or not isinstance(publication["publicationId"], str)
            or not isinstance(publication["source"], str)
            or receive_capability(publication["source"]) not in scope.capabilities
            or type(publication["publicationEpoch"]) is not int
            or not 1 <= publication["publicationEpoch"] < 2**53
            or (publication["peerId"], publication["publicationId"]) in seen
            or not any(
                g["publisherPeerId"] == publication["peerId"] and publication["publicationId"] in g["publicationIds"]
                for g in value["grants"]
            )
        ):
            raise MeetError("meet_authorization_publications_invalid", 502)
        seen.add((publication["peerId"], publication["publicationId"]))
    if seen != {(g["publisherPeerId"], p) for g in value["grants"] for p in g["publicationIds"]}:
        raise MeetError("meet_authorization_publications_invalid", 502)
    return value


class MeetAuthorizationClient:
    def __init__(self, authority, issuer, clock=time.time):
        self.authority, self.issuer, self.clock = authority, issuer, clock

    def inspect(self, task_id, lease_id, runtime_id, session_id):
        return self._inspect(task_id, lease_id, runtime_id, session_id, "authorization", validate_authorization)

    def observe(self, task_id, lease_id, runtime_id, session_id):
        from agent.models.meet_session_observation import validate_observation

        return self._inspect(task_id, lease_id, runtime_id, session_id, "observation", validate_observation)

    def retire(self, task_id, lease_id, runtime_id, session_id):
        from agent.models.meet_session_retirement import validate_retirement

        return self._inspect(task_id, lease_id, runtime_id, session_id, "retire", validate_retirement)

    def _inspect(self, task_id, lease_id, runtime_id, session_id, endpoint, validate):
        if not isinstance(session_id, str) or not re.fullmatch(r"ms_[A-Za-z0-9_-]{32}", session_id):
            raise MeetError("meet_authorization_session_invalid")
        scope = self.authority.current(task_id, lease_id, runtime_id)
        meeting = self.issuer.issue_dialog(self.authority, task_id, lease_id, runtime_id, self.clock())
        nonce = secrets.token_hex(16)
        body = encode({"roomId": scope.room_id, "sessionId": session_id, "nonce": nonce})

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                raise MeetError("meet_authorization_redirect_denied", 502)

        request = urllib.request.Request(
            scope.origin + "/api/machine/sessions/" + endpoint,
            body,
            {"Content-Type": "application/json", "Authorization": "Bearer " + meeting["grant"]},
        )
        try:
            from ananta_contracts.meet_dialog import (
                MAX_DIALOG_BYTES,
                MAX_VISUAL_CONTROL_BYTES,
                parse,
                parse_visual_control,
            )

            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            deadline = time.monotonic() + 3
            visual = endpoint == "authorization" and "video.receive" in scope.capabilities
            with opener.open(request, timeout=3) as response:
                raw = read_bounded(
                    response, maximum=MAX_VISUAL_CONTROL_BYTES if visual else MAX_DIALOG_BYTES, deadline=deadline
                )
            result = validate(
                (parse_visual_control if visual else parse)(raw),
                scope,
                self.issuer.issuer,
                session_id,
                nonce,
                int(self.clock() * 1000),
            )
            current = self.authority.current(task_id, lease_id, runtime_id)
            # Source CAS is checked independently by each fresh projection.
            # A profile switch must not revoke the parent membership or speech.
            # Negotiation presence and every actual membership binding stay fixed.
            if (
                (current.avatar_selection is None) != (scope.avatar_selection is None)
                or (current.voice_selection is None) != (scope.voice_selection is None)
                or current
                != replace(
                    scope,
                    controls=current.controls,
                    avatar_selection=current.avatar_selection,
                    voice_selection=current.voice_selection,
                )
            ):
                raise MeetError("meet_authorization_changed", 409)
            return result
        except MeetError:
            raise
        except Exception as error:
            if isinstance(error, urllib.error.HTTPError):
                try:
                    error.close()
                except OSError:
                    pass
            if transient_http_read_error(error):
                raise MeetError("meet_authorization_unavailable", 503) from None
            raise MeetError("meet_authorization_failed", 502) from None
