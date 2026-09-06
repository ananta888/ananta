"""TLS backchannel to Meet's current control-plane state, not Worker assertions."""

import json
import re
import secrets
import time
import urllib.request
from dataclasses import replace

from agent.services.meet_contract import MeetError
from worker.meet_media.contract import encode
from worker.meet_media.persona_http import read_bounded


def validate_authorization(value, scope, issuer, session_id, nonce, now_ms):
    if not isinstance(value, dict) or set(value) != {"schema", "nonce", "lease", "binding", "peerId", "roomId", "membershipEpoch", "receiveRevision", "grants", "publications"}:
        raise MeetError("meet_authorization_contract_invalid", 502)
    expected = {"issuer": issuer, "subject": "machine:ananta", "roomId": scope.room_id, "taskId": scope.task_id,
                "tenantId": scope.tenant_id, "projectId": scope.project_id, "protocolVersion": "v2",
                "runtimeId": scope.runtime_id, "hubSessionId": scope.session_id, "capabilitySet": ",".join(scope.capabilities)}
    if value["schema"] != "ananta.meet-authorization.v1" or value["nonce"] != nonce or value["binding"] != expected or value["roomId"] != scope.room_id:
        raise MeetError("meet_authorization_scope_invalid", 502)
    lease = value["lease"]
    if (not isinstance(lease, dict) or set(lease) != {"schema", "sessionId", "generation", "expiresAt", "absoluteExpiresAt"}
            or lease["schema"] != "ananta.meet-session-lease.v1" or lease["sessionId"] != session_id
            or type(lease["generation"]) is not int or not 1 <= lease["generation"] <= 512
            or type(lease["expiresAt"]) is not int or not now_ms < lease["expiresAt"] <= min(now_ms + 600_000, scope.deadline * 1000)
            or type(lease["absoluteExpiresAt"]) is not int or not lease["expiresAt"] <= lease["absoluteExpiresAt"] <= now_ms + 7_200_000
            or not isinstance(value["peerId"], str) or not re.fullmatch(r"[a-f0-9]{16}", value["peerId"])
            or type(value["membershipEpoch"]) is not int or not 1 <= value["membershipEpoch"] < 2**53
            or type(value["receiveRevision"]) is not int or not 0 <= value["receiveRevision"] < 2**53
            or not isinstance(value["grants"], list) or len(value["grants"]) > 19):
        raise MeetError("meet_authorization_state_invalid", 502)
    seen = set()
    for grant in value["grants"]:
        if (not isinstance(grant, dict) or set(grant) != {"publisherPeerId", "machinePeerId", "publicationIds", "chatRead", "expiresAt"}
                or not isinstance(grant["publisherPeerId"], str) or not re.fullmatch(r"[a-f0-9]{16}", grant["publisherPeerId"])
                or grant["publisherPeerId"] == value["peerId"] or grant["publisherPeerId"] in seen
                or grant["machinePeerId"] != value["peerId"] or type(grant["chatRead"]) is not bool
                or type(grant["expiresAt"]) is not int or not now_ms < grant["expiresAt"] <= now_ms + 600_000
                or not isinstance(grant["publicationIds"], list) or len(grant["publicationIds"]) > 2
                or any(not isinstance(p, str) or not re.fullmatch(r"[A-Za-z0-9_={}:-]{1,128}", p) for p in grant["publicationIds"])
                or len(set(grant["publicationIds"])) != len(grant["publicationIds"])):
            raise MeetError("meet_authorization_grant_invalid", 502)
        seen.add(grant["publisherPeerId"])
    publications = value["publications"]
    if not isinstance(publications, list) or len(publications) > 38:
        raise MeetError("meet_authorization_publications_invalid", 502)
    seen = set()
    for publication in publications:
        if (not isinstance(publication, dict) or set(publication) != {"peerId", "publicationId", "source", "publicationEpoch"}
                or not isinstance(publication["peerId"], str) or not isinstance(publication["publicationId"], str)
                or not isinstance(publication["source"], str) or publication["source"] not in {"microphone", "screen-audio"}
                or type(publication["publicationEpoch"]) is not int or not 1 <= publication["publicationEpoch"] < 2**53
                or (publication["peerId"], publication["publicationId"]) in seen
                or not any(g["publisherPeerId"] == publication["peerId"] and publication["publicationId"] in g["publicationIds"] for g in value["grants"])):
            raise MeetError("meet_authorization_publications_invalid", 502)
        seen.add((publication["peerId"], publication["publicationId"]))
    if seen != {(g["publisherPeerId"], p) for g in value["grants"] for p in g["publicationIds"]}:
        raise MeetError("meet_authorization_publications_invalid", 502)
    return value


class MeetAuthorizationClient:
    def __init__(self, authority, issuer, clock=time.time):
        self.authority, self.issuer, self.clock = authority, issuer, clock

    def inspect(self, task_id, lease_id, runtime_id, session_id):
        if not isinstance(session_id, str) or not re.fullmatch(r"ms_[A-Za-z0-9_-]{32}", session_id):
            raise MeetError("meet_authorization_session_invalid")
        scope = self.authority.current(task_id, lease_id, runtime_id)
        meeting = self.issuer.issue_dialog(self.authority, task_id, lease_id, runtime_id, self.clock())
        nonce = secrets.token_hex(16)
        body = encode({"roomId": scope.room_id, "sessionId": session_id, "nonce": nonce})

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                raise MeetError("meet_authorization_redirect_denied", 502)

        request = urllib.request.Request(scope.origin + "/api/machine/sessions/authorization", body,
            {"Content-Type": "application/json", "Authorization": "Bearer " + meeting["grant"]})
        try:
            from ananta_contracts.meet_dialog import parse
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            deadline = time.monotonic() + 3
            with opener.open(request, timeout=3) as response:
                raw = read_bounded(response, maximum=16_384, deadline=deadline)
            result = validate_authorization(parse(raw), scope, self.issuer.issuer, session_id, nonce, int(self.clock() * 1000))
            current = self.authority.current(task_id, lease_id, runtime_id)
            if current != replace(scope, controls=current.controls):
                raise MeetError("meet_authorization_changed", 409)
            return result
        except MeetError:
            raise
        except Exception:
            raise MeetError("meet_authorization_unavailable", 503) from None
