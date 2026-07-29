"""Worker-to-Hub admission client for revocable Vector-Index dispatches."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from urllib.parse import quote

_MAX_RESPONSE_BYTES = 16_384
_ADMISSION_GRANTED_REASON = "vector_index_dispatch_admission_granted"
_SUCCESS_ENVELOPE_FIELDS = frozenset({"status", "data"})
_SUCCESS_DATA_FIELDS = frozenset(
    {
        "allowed",
        "reason_code",
        "job_id",
        "attempt_id",
        "sequence",
        "phase",
        "worker_audience",
    }
)


class VectorIndexDispatchAdmissionPort(Protocol):
    def admit(
        self,
        *,
        job_id: str,
        attempt_id: str,
        sequence: int,
        phase: str,
        worker_audience: str,
    ) -> None: ...


class HubVectorIndexDispatchAdmissionClient:
    """Redeem an execute grant at the Hub immediately before mutation."""

    def __init__(
        self,
        *,
        hub_url: str,
        worker_id: str,
        worker_url: str,
        token_provider: Callable[[], str | None],
        timeout_seconds: float = 3.0,
        post: Callable[..., Any] | None = None,
    ) -> None:
        from ananta_contracts.vector_index_dispatch import (
            canonicalize_vector_index_worker_audience,
        )

        self._hub_url = canonicalize_vector_index_worker_audience(
            hub_url
        )
        self._worker_url = canonicalize_vector_index_worker_audience(
            worker_url
        )
        self._worker_id = str(worker_id or "").strip()
        if (
            not self._worker_id
            or len(self._worker_id.encode("utf-8")) > 256
        ):
            raise RuntimeError(
                "vector_index_dispatch_worker_id_invalid"
            )
        self._token_provider = token_provider
        self._timeout_seconds = max(
            0.5,
            min(float(timeout_seconds), 10.0),
        )
        self._post = post

    def admit(
        self,
        *,
        job_id: str,
        attempt_id: str,
        sequence: int,
        phase: str,
        worker_audience: str,
    ) -> None:
        if worker_audience != self._worker_url:
            raise RuntimeError(
                "vector_index_dispatch_admission_audience_mismatch"
            )
        expected_job_id = str(job_id)
        expected_attempt_id = str(attempt_id)
        expected_phase = str(phase)
        token = str(self._token_provider() or "").strip()
        if len(token.encode("utf-8")) < 32:
            raise RuntimeError(
                "vector_index_dispatch_worker_identity_unavailable"
            )
        post = self._post
        if post is None:
            import requests

            post = requests.post
        try:
            response = post(
                (
                    self._hub_url
                    + "/internal/tasks/"
                    + quote(expected_job_id, safe="")
                    + "/vector-index-dispatch-admission"
                ),
                json={
                    "attempt_id": expected_attempt_id,
                    "sequence": sequence,
                    "phase": expected_phase,
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Ananta-Worker-ID": self._worker_id,
                    "X-Ananta-Worker-URL": self._worker_url,
                },
                timeout=self._timeout_seconds,
                allow_redirects=False,
                stream=True,
            )
            status_code = int(
                getattr(response, "status_code", 500) or 500
            )
            if 300 <= status_code < 400:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                raise RuntimeError(
                    "vector_index_dispatch_admission_redirect"
                )
            body = self._bounded_json(response)
            payload = (
                body.get("data")
                if isinstance(body, Mapping)
                else None
            )
            if status_code >= 400:
                reason = (
                    str(
                        (payload or {}).get("reason_code")
                        or ""
                    ).strip()
                    or "vector_index_dispatch_admission_denied"
                )
                raise RuntimeError(reason)
            expected_payload = {
                "allowed": True,
                "reason_code": _ADMISSION_GRANTED_REASON,
                "job_id": expected_job_id,
                "attempt_id": expected_attempt_id,
                "sequence": sequence,
                "phase": expected_phase,
                "worker_audience": self._worker_url,
            }
            if (
                status_code != 200
                or not isinstance(body, Mapping)
                or set(body) != _SUCCESS_ENVELOPE_FIELDS
                or body.get("status") != "success"
                or not isinstance(payload, Mapping)
                or set(payload) != _SUCCESS_DATA_FIELDS
                or dict(payload) != expected_payload
            ):
                raise RuntimeError(
                    "vector_index_dispatch_admission_response_invalid"
                )
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                "vector_index_dispatch_admission_unavailable"
            ) from exc

    @staticmethod
    def _bounded_json(response: Any) -> Any:
        try:
            headers = getattr(response, "headers", None)
            declared = (
                headers.get("Content-Length")
                if isinstance(headers, Mapping)
                else None
            )
            if (
                declared is not None
                and int(declared) > _MAX_RESPONSE_BYTES
            ):
                raise RuntimeError(
                    "vector_index_dispatch_admission_response_too_large"
                )
            chunks: list[bytes] = []
            total = 0
            iterator = getattr(response, "iter_content", None)
            values = (
                iterator(
                    chunk_size=4096,
                    decode_unicode=False,
                )
                if callable(iterator)
                else (getattr(response, "content", b""),)
            )
            for chunk in values:
                if not isinstance(chunk, bytes):
                    raise RuntimeError(
                        "vector_index_dispatch_admission_response_invalid"
                    )
                total += len(chunk)
                if total > _MAX_RESPONSE_BYTES:
                    raise RuntimeError(
                        "vector_index_dispatch_admission_response_too_large"
                    )
                chunks.append(chunk)
            return json.loads(b"".join(chunks))
        except (TypeError, ValueError, UnicodeError) as exc:
            raise RuntimeError(
                "vector_index_dispatch_admission_response_invalid"
            ) from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()


def build_vector_index_dispatch_admission_client(
) -> HubVectorIndexDispatchAdmissionClient:
    from agent.auth import resolve_configured_agent_token
    from agent.config import settings

    return HubVectorIndexDispatchAdmissionClient(
        hub_url=str(settings.hub_url or ""),
        worker_id=str(settings.agent_name or ""),
        worker_url=str(
            settings.agent_url
            or f"http://localhost:{settings.port}"
        ),
        token_provider=resolve_configured_agent_token,
    )


__all__ = [
    "HubVectorIndexDispatchAdmissionClient",
    "VectorIndexDispatchAdmissionPort",
    "build_vector_index_dispatch_admission_client",
]
