"""Thin HTTP-only CLI for model-intelligence jobs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Mapping, Protocol, Sequence
from urllib import error, parse, request


EXIT_SUCCESS = 0
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_CONFLICT = 5
EXIT_UNAVAILABLE = 6
EXIT_API_ERROR = 7


class ModelIntelligenceCliError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes


class HttpTransport(Protocol):
    def send(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        """Send one HTTP request."""


class UrllibHttpTransport:
    def send(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        call = request.Request(
            url=url,
            method=method,
            data=body,
            headers=dict(headers),
        )
        try:
            with request.urlopen(call, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status_code=response.status,
                    body=response.read(),
                )
        except error.HTTPError as exc:
            return HttpResponse(
                status_code=exc.code,
                body=exc.read(),
            )
        except error.URLError as exc:
            raise ModelIntelligenceCliError(
                "model_intelligence_api_unavailable",
                "Model-intelligence API is unavailable.",
            ) from exc


class ModelIntelligenceApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str | None = None,
        timeout_seconds: float = 30.0,
        transport: HttpTransport | None = None,
    ) -> None:
        normalised = base_url.rstrip("/")
        parsed = parse.urlsplit(normalised)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._base_url = normalised
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibHttpTransport()

    def create_job(
        self,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self._json_request(
            "POST",
            "/api/model-intelligence/jobs",
            payload=payload,
            extra_headers={"Idempotency-Key": idempotency_key},
        )

    def list_jobs(
        self,
        *,
        page_size: int = 50,
        cursor: str | None = None,
    ) -> Mapping[str, object]:
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        query = {"page_size": str(page_size)}
        if cursor:
            query["cursor"] = cursor
        return self._json_request(
            "GET",
            f"/api/model-intelligence/jobs?{parse.urlencode(query)}",
        )

    def get_job(self, job_id: str) -> Mapping[str, object]:
        return self._json_request(
            "GET",
            f"/api/model-intelligence/jobs/{self._segment(job_id)}",
        )

    def cancel_job(self, job_id: str) -> Mapping[str, object]:
        return self._json_request(
            "POST",
            f"/api/model-intelligence/jobs/{self._segment(job_id)}/cancel",
            payload={},
        )

    def get_artifact(
        self,
        artifact_id: str,
    ) -> Mapping[str, object]:
        return self._json_request(
            "GET",
            f"/api/model-intelligence/artifacts/{self._segment(artifact_id)}",
        )

    def get_report(self, job_id: str) -> Mapping[str, object]:
        return self._json_request(
            "GET",
            f"/api/model-intelligence/jobs/{self._segment(job_id)}/report",
        )

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "ananta-model-intelligence-cli/1",
        }
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        body = None
        if payload is not None:
            body = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        response = self._transport.send(
            method=method,
            url=f"{self._base_url}{path}",
            headers=headers,
            body=body,
            timeout_seconds=self._timeout_seconds,
        )
        decoded = self._decode_response(response)
        if not 200 <= response.status_code < 300:
            raise ModelIntelligenceCliError(
                str(decoded.get("reason_code") or "model_intelligence_api_error"),
                str(decoded.get("message") or "Model-intelligence API request failed."),
                status_code=response.status_code,
            )
        return decoded

    @staticmethod
    def _decode_response(response: HttpResponse) -> Mapping[str, object]:
        try:
            value = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelIntelligenceCliError(
                "model_intelligence_response_invalid",
                "Model-intelligence API returned invalid JSON.",
                status_code=response.status_code,
            ) from exc
        if not isinstance(value, Mapping):
            raise ModelIntelligenceCliError(
                "model_intelligence_response_invalid",
                "Model-intelligence API response must be a JSON object.",
                status_code=response.status_code,
            )
        return value

    @staticmethod
    def _segment(value: str) -> str:
        if not value:
            raise ValueError("resource ID must not be empty")
        return parse.quote(value, safe="")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ananta-model-intelligence")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ANANTA_BASE_URL", "http://127.0.0.1:5000"),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create")
    create.add_argument("--request-json", type=Path, required=True)
    create.add_argument("--idempotency-key", required=True)

    listing = commands.add_parser("list")
    listing.add_argument("--page-size", type=int, default=50)
    listing.add_argument("--cursor")

    get = commands.add_parser("get")
    get.add_argument("job_id")

    cancel = commands.add_parser("cancel")
    cancel.add_argument("job_id")

    artifact = commands.add_parser("artifact")
    artifact.add_argument("artifact_id")

    report = commands.add_parser("report")
    report.add_argument("job_id")
    return parser


def _load_request(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelIntelligenceCliError(
            "model_intelligence_request_invalid",
            "Request file must contain a readable JSON object.",
        ) from exc
    if not isinstance(value, Mapping):
        raise ModelIntelligenceCliError(
            "model_intelligence_request_invalid",
            "Request file must contain a JSON object.",
        )
    return value


def _exit_code(exc: ModelIntelligenceCliError) -> int:
    if exc.status_code in {401, 403}:
        return EXIT_AUTH
    if exc.status_code == 404:
        return EXIT_NOT_FOUND
    if exc.status_code in {409, 412, 429}:
        return EXIT_CONFLICT
    if exc.status_code is None or exc.status_code >= 500:
        return EXIT_UNAVAILABLE
    return EXIT_API_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    client = ModelIntelligenceApiClient(
        base_url=args.base_url,
        bearer_token=os.environ.get("ANANTA_API_TOKEN"),
        timeout_seconds=args.timeout,
    )
    try:
        if args.command == "create":
            result = client.create_job(
                _load_request(args.request_json),
                idempotency_key=args.idempotency_key,
            )
        elif args.command == "list":
            result = client.list_jobs(
                page_size=args.page_size,
                cursor=args.cursor,
            )
        elif args.command == "get":
            result = client.get_job(args.job_id)
        elif args.command == "cancel":
            result = client.cancel_job(args.job_id)
        elif args.command == "artifact":
            result = client.get_artifact(args.artifact_id)
        else:
            result = client.get_report(args.job_id)
    except ModelIntelligenceCliError as exc:
        print(
            json.dumps(
                {
                    "reason_code": exc.reason_code,
                    "message": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return _exit_code(exc)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
