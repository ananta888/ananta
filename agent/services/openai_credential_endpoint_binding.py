"""Bind the server-side OpenAI credential to one configured endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

OPENAI_API_KEY_REFERENCE = "env://OPENAI_API_KEY"
_SUPPORTED_PATHS = {"/v1", "/v1/chat/completions"}


class OpenAICredentialEndpointBindingError(ValueError):
    """A safe, stable failure raised before resolving or sending a credential."""

    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True)
class OpenAICredentialEndpointBinding:
    chat_completions_url: str
    models_url: str


def bind_openai_credential_endpoint(
    *,
    client_api_base: str | None,
    trusted_api_url: str,
    credential_ref: str | None = None,
) -> OpenAICredentialEndpointBinding:
    """Return canonical endpoints only when credential and endpoint are bound.

    AI-Snake runtime supports only the server-side ``OPENAI_API_KEY``. The
    browser may omit that reference or name it explicitly, but it cannot select
    another server environment variable or redirect the credential to a
    different origin/path than the operator-configured ``OPENAI_URL``.
    """

    reference = str(credential_ref or "").strip()
    if reference not in {"", OPENAI_API_KEY_REFERENCE}:
        raise OpenAICredentialEndpointBindingError("unsupported_credential_reference")

    trusted = _normalize_openai_api_url(trusted_api_url)
    candidate = _normalize_openai_api_url(client_api_base or trusted_api_url)
    if candidate != trusted:
        raise OpenAICredentialEndpointBindingError("openai_endpoint_credential_mismatch")
    return trusted


def _normalize_openai_api_url(value: str) -> OpenAICredentialEndpointBinding:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise OpenAICredentialEndpointBindingError("invalid_endpoint") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise OpenAICredentialEndpointBindingError("invalid_endpoint")

    path = parsed.path.rstrip("/") or "/"
    if path not in _SUPPORTED_PATHS:
        raise OpenAICredentialEndpointBindingError("invalid_endpoint")

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    default_port = 443
    normalized_port = port if port is not None else default_port
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host if normalized_port == default_port else f"{host}:{normalized_port}"
    origin = urlunsplit((scheme, netloc, "", "", ""))
    return OpenAICredentialEndpointBinding(
        chat_completions_url=f"{origin}/v1/chat/completions",
        models_url=f"{origin}/v1/models",
    )
