"""Pair/session-bound, non-global speech evidence identity commitments."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")


class SpeechEvidenceIdentityError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechEvidenceIdentity:
    algorithm_version: str
    pair_id: str
    session_id: str
    session_epoch: int
    speaker_scope: str
    capture_segment_id: str
    start_ms: int
    end_ms: int
    source_digest: str
    source_scope_digest: str
    utterance_family_id: str
    evidence_revision_id: str
    speaker_scope_digest: str


class SpeechEvidenceIdentityService:
    ALGORITHM_VERSION = "speech-evidence-commitment-hmac-sha256-v1"

    def __init__(self, scope_key: bytes) -> None:
        if len(scope_key) < 32:
            raise SpeechEvidenceIdentityError("speech_identity_key_invalid")
        self._root = bytes(scope_key)

    def identify(
        self,
        *,
        pair_id: str,
        session_id: str,
        session_epoch: int,
        speaker_scope: str,
        capture_segment_id: str,
        start_ms: int,
        end_ms: int,
        source_digest: str,
        revision: int,
        revision_digest: str,
    ) -> SpeechEvidenceIdentity:
        for value in (pair_id, session_id, speaker_scope, capture_segment_id):
            if _SAFE.fullmatch(value) is None:
                raise SpeechEvidenceIdentityError("speech_identity_scope_invalid")
        if _DIGEST.fullmatch(source_digest) is None or _DIGEST.fullmatch(revision_digest) is None:
            raise SpeechEvidenceIdentityError("speech_identity_digest_invalid")
        if (
            isinstance(session_epoch, bool)
            or session_epoch < 1
            or isinstance(revision, bool)
            or revision < 1
            or isinstance(start_ms, bool)
            or isinstance(end_ms, bool)
            or start_ms < 0
            or end_ms <= start_ms
            or end_ms - start_ms > 3_600_000
        ):
            raise SpeechEvidenceIdentityError("speech_identity_range_invalid")
        scope_material = _join(
            self.ALGORITHM_VERSION,
            pair_id,
            session_id,
            str(session_epoch),
            speaker_scope,
        )
        scope_key = hmac.new(self._root, b"scope\0" + scope_material, hashlib.sha256).digest()
        speaker_digest = hmac.new(scope_key, b"speaker\0" + speaker_scope.encode(), hashlib.sha256).hexdigest()
        source_scope_digest = hmac.new(
            scope_key,
            b"source\0" + source_digest.encode(),
            hashlib.sha256,
        ).hexdigest()
        # The family excludes transcript/revision material, but includes the
        # capture segment and exact source bounds so reconnect segments and
        # repeated/still audio do not collapse accidentally.
        family_material = _join(
            self.ALGORITHM_VERSION,
            pair_id,
            session_id,
            str(session_epoch),
            speaker_scope,
            capture_segment_id,
            str(start_ms),
            str(end_ms),
            source_digest,
        )
        family = hmac.new(scope_key, b"family\0" + family_material, hashlib.sha256).hexdigest()
        revision_material = _join(family, str(revision), revision_digest)
        revision_id = hmac.new(scope_key, b"revision\0" + revision_material, hashlib.sha256).hexdigest()
        return SpeechEvidenceIdentity(
            algorithm_version=self.ALGORITHM_VERSION,
            pair_id=pair_id,
            session_id=session_id,
            session_epoch=session_epoch,
            speaker_scope=speaker_scope,
            capture_segment_id=capture_segment_id,
            start_ms=start_ms,
            end_ms=end_ms,
            source_digest=source_digest,
            source_scope_digest=source_scope_digest,
            utterance_family_id=f"utterance-v1:{family}",
            evidence_revision_id=f"evidence-revision-v1:{revision_id}",
            speaker_scope_digest=speaker_digest,
        )


def _join(*values: str) -> bytes:
    return "\0".join(values).encode("utf-8")


__all__ = [
    "SpeechEvidenceIdentity",
    "SpeechEvidenceIdentityError",
    "SpeechEvidenceIdentityService",
]
