from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping, Protocol, Sequence
from urllib.parse import unquote, urlsplit

from agent.services.mail_contract_service import MailAccountV2
from agent.services.mail_provider_ports import MailAuthMaterial, MailProviderResult


class MailSecretResolutionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or "mail_secret_resolution_failed")
        super().__init__(self.reason_code)


class MailSecretResolver(Protocol):
    def resolve(self, reference: str) -> str:
        ...


class StoreMailSecretResolver:
    def __init__(self, *, store: object, allow_insecure_legacy_store: bool = False) -> None:
        self._store = store
        self._allow_insecure_legacy_store = bool(allow_insecure_legacy_store)

    def resolve(self, reference: str) -> str:
        clean_reference = str(reference or "").strip()
        if not clean_reference:
            raise MailSecretResolutionError("mail_secret_ref_required")
        getter = getattr(self._store, "get_secret", None)
        if not callable(getter):
            raise MailSecretResolutionError("mail_secret_store_invalid")
        try:
            result = getter(credential_ref=clean_reference)
        except Exception as exc:
            raise MailSecretResolutionError("mail_secret_store_unavailable") from exc
        if not isinstance(result, Mapping) or not bool(result.get("ok")):
            raise MailSecretResolutionError(str(getattr(result, "get", lambda *_: "")("reason_code") or "mail_secret_not_found"))
        warning = str(result.get("warning_reason_code") or "")
        if warning == "insecure_fallback_storage" and not self._allow_insecure_legacy_store:
            raise MailSecretResolutionError("mail_insecure_secret_store_forbidden")
        secret = result.get("secret")
        if not isinstance(secret, str) or not secret:
            raise MailSecretResolutionError("mail_secret_empty")
        return secret


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvFileMailSecretResolver:
    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        allowed_file_roots: Sequence[str | Path] = (Path("/run/secrets"),),
        maximum_bytes: int = 16 * 1024,
    ) -> None:
        self._environ = environ if environ is not None else os.environ
        self._roots = tuple(Path(root).resolve(strict=False) for root in allowed_file_roots)
        self._maximum_bytes = max(1, int(maximum_bytes))
        if not self._roots:
            raise ValueError("mail_secret_roots_required")

    def resolve(self, reference: str) -> str:
        parsed = urlsplit(str(reference or "").strip())
        if parsed.query or parsed.fragment or parsed.username is not None or parsed.password is not None:
            raise MailSecretResolutionError("mail_secret_ref_invalid")
        if parsed.scheme == "env":
            name = str(parsed.netloc or "")
            if parsed.path or not _ENV_NAME.fullmatch(name):
                raise MailSecretResolutionError("mail_env_secret_ref_invalid")
            value = self._environ.get(name)
            if not isinstance(value, str) or not value:
                raise MailSecretResolutionError("mail_secret_not_found")
            return value
        if parsed.scheme != "file":
            raise MailSecretResolutionError("mail_secret_ref_scheme_unsupported")
        if parsed.netloc not in {"", "localhost"}:
            raise MailSecretResolutionError("mail_file_secret_ref_invalid")
        source = Path(unquote(parsed.path))
        if not source.is_absolute():
            raise MailSecretResolutionError("mail_secret_path_must_be_absolute")
        if source.is_symlink():
            raise MailSecretResolutionError("mail_secret_symlink_forbidden")
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise MailSecretResolutionError("mail_secret_not_found") from exc
        if not any(resolved.is_relative_to(root) for root in self._roots):
            raise MailSecretResolutionError("mail_secret_path_not_allowed")
        if not resolved.is_file():
            raise MailSecretResolutionError("mail_secret_not_regular_file")
        try:
            with resolved.open("rb") as handle:
                raw = handle.read(self._maximum_bytes + 1)
        except OSError as exc:
            raise MailSecretResolutionError("mail_secret_unreadable") from exc
        if len(raw) > self._maximum_bytes:
            raise MailSecretResolutionError("mail_secret_too_large")
        try:
            value = raw.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as exc:
            raise MailSecretResolutionError("mail_secret_encoding_invalid") from exc
        if not value:
            raise MailSecretResolutionError("mail_secret_empty")
        return value


class CompositeMailSecretResolver:
    def __init__(self, *, reference_resolver: MailSecretResolver, store_resolver: MailSecretResolver) -> None:
        self._reference_resolver = reference_resolver
        self._store_resolver = store_resolver

    def resolve(self, reference: str) -> str:
        scheme = urlsplit(str(reference or "").strip()).scheme.lower()
        if scheme in {"env", "file"}:
            return self._reference_resolver.resolve(reference)
        return self._store_resolver.resolve(reference)


class MailAccountAuthResolver:
    def __init__(self, *, username_resolver: MailSecretResolver, credential_resolver: MailSecretResolver) -> None:
        self._username_resolver = username_resolver
        self._credential_resolver = credential_resolver

    def resolve(self, account: MailAccountV2) -> MailProviderResult[MailAuthMaterial]:
        try:
            username = self._username_resolver.resolve(account.username_ref)
            credential = self._credential_resolver.resolve(account.credential_ref)
        except MailSecretResolutionError as exc:
            return MailProviderResult(
                ok=False,
                reason_code=exc.reason_code,
                retryable=False,
            )
        return MailProviderResult(
            ok=True,
            reason_code="ok",
            value=MailAuthMaterial(username=username, credential=credential),
        )


_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(authorization|password|token|access_token|refresh_token|secret)(\s*[:=]\s*)([^\s,;]+)"
)


def redact_mail_secret_text(value: object, *, known_secrets: Sequence[str] = ()) -> str:
    redacted = str(value)
    for secret in known_secrets:
        clean = str(secret or "")
        if clean:
            redacted = redacted.replace(clean, "[REDACTED_SECRET]")
    return _SENSITIVE_TEXT.sub(r"\1\2[REDACTED_SECRET]", redacted)


__all__ = [
    "CompositeMailSecretResolver",
    "EnvFileMailSecretResolver",
    "MailAccountAuthResolver",
    "MailSecretResolutionError",
    "MailSecretResolver",
    "StoreMailSecretResolver",
    "redact_mail_secret_text",
]
