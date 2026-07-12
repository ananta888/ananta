from __future__ import annotations

import base64
import hashlib
import os

from agent.config import settings
from agent.services.voice_governance_domain import VoiceGovernanceError


class VoiceSensitiveTextCodec:
    """Encrypts user-owned transcript corrections before Hub persistence."""

    _PREFIX = "enc:v1:"

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        token = self._fernet().encrypt(value.encode("utf-8")).decode("ascii")
        return f"{self._PREFIX}{token}"

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(self._PREFIX):
            raise VoiceGovernanceError(
                code="voice_governance.unencrypted_sensitive_value",
                message="stored voice personalization value is not encrypted",
                status_code=500,
            )
        try:
            decrypted: bytes = self._fernet().decrypt(value[len(self._PREFIX) :].encode("ascii"))
            return decrypted.decode("utf-8")
        except Exception as exc:
            raise VoiceGovernanceError(
                code="voice_governance.decryption_failed",
                message="stored voice personalization value could not be decrypted",
                status_code=500,
            ) from exc

    @staticmethod
    def _fernet():
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise VoiceGovernanceError(
                code="voice_governance.encryption_unavailable",
                message="voice personalization encryption support is unavailable",
                status_code=503,
            ) from exc

        configured = str(os.environ.get("VOICE_PERSONALIZATION_ENCRYPTION_KEY") or "").strip()
        if configured:
            try:
                return Fernet(configured.encode("ascii"))
            except (TypeError, ValueError) as exc:
                raise VoiceGovernanceError(
                    code="voice_governance.invalid_encryption_key",
                    message="VOICE_PERSONALIZATION_ENCRYPTION_KEY is not a valid Fernet key",
                    status_code=500,
                ) from exc

        secret = str(settings.secret_key or "")
        if not secret:
            raise VoiceGovernanceError(
                code="voice_governance.encryption_key_missing",
                message="voice personalization encryption key is not configured",
                status_code=500,
            )
        digest = hashlib.sha256(f"ananta-voice-personalization-v1:{secret}".encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


voice_sensitive_text_codec = VoiceSensitiveTextCodec()


def get_voice_sensitive_text_codec() -> VoiceSensitiveTextCodec:
    return voice_sensitive_text_codec
