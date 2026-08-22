#!/usr/bin/env python3
"""Bounded command-line client for the public HRM experiment API."""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

_MAX_FILE_BYTES = 2_000_000
_MAX_RESPONSE_BYTES = 4_000_000


class ClientError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _load_json(path: str) -> dict[str, Any]:
    source = Path(path)
    size = source.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise ClientError("request_file_too_large")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClientError("request_file_invalid") from exc
    if not isinstance(value, dict):
        raise ClientError("request_object_required")
    return value


def _token(path: str) -> str:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ClientError("token_file_unavailable") from exc
    if not 32 <= len(value.encode("utf-8")) <= 16_384 or any(
        character.isspace() for character in value
    ):
        raise ClientError("token_file_invalid")
    return value


class HrmExperimentsClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        allow_http: bool = False,
        timeout_seconds: float = 20.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ClientError("base_url_invalid")
        if parsed.scheme != "https" and not (
            allow_http and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ClientError("https_required")
        self._base_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
        self._token = token
        self._timeout = max(1.0, min(float(timeout_seconds), 120.0))
        self._opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str | int | None] | None = None,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        suffix = urllib.parse.urlencode(
            {key: value for key, value in (query or {}).items() if value is not None}
        )
        url = f"{self._base_url}/api/hrm-experiments/{path.lstrip('/')}"
        if suffix:
            url = f"{url}?{suffix}"
        encoded = None
        if body is not None:
            encoded = json.dumps(
                body,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if len(encoded) > _MAX_FILE_BYTES:
                raise ClientError("request_body_too_large")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "ananta-hrm-experiments-client/1",
        }
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            if not 8 <= len(idempotency_key) <= 191 or any(
                character.isspace() for character in idempotency_key
            ):
                raise ClientError("idempotency_key_invalid")
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            url, data=encoded, method=method.upper(), headers=headers
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raw = exc.read(_MAX_RESPONSE_BYTES + 1)
            reason = f"http_{exc.code}"
            try:
                problem = json.loads(raw.decode("utf-8"))
                reason = str(
                    problem.get("reason_code")
                    or ((problem.get("error") or {}).get("code"))
                    or reason
                )
            except Exception:
                pass
            raise ClientError(reason) from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise ClientError("hub_unavailable") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ClientError("response_too_large")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ClientError("response_invalid") from exc
        if not isinstance(value, dict):
            raise ClientError("response_object_required")
        data = value.get("data")
        return data if isinstance(data, dict) else value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument("--timeout", type=float, default=20.0)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("capabilities")
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--request", required=True)

    for name in ("datasets-list", "checkpoints-list", "runs-list"):
        command = commands.add_parser(name)
        command.add_argument("--project-id", required=True)
        command.add_argument("--cursor")
        command.add_argument("--limit", type=int, default=50)

    for name in ("dataset-register", "checkpoint-admit", "run-start"):
        command = commands.add_parser(name)
        command.add_argument("--request", required=True)
        command.add_argument("--idempotency-key", required=True)

    for name in ("run-status", "run-events"):
        command = commands.add_parser(name)
        command.add_argument("--project-id", required=True)
        command.add_argument("--run-id", required=True)
        if name == "run-events":
            command.add_argument("--cursor")
            command.add_argument("--limit", type=int, default=50)

    cancel = commands.add_parser("run-cancel")
    cancel.add_argument("--project-id", required=True)
    cancel.add_argument("--run-id", required=True)
    cancel.add_argument("--request", required=True)
    cancel.add_argument("--idempotency-key", required=True)

    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--project-id", required=True)
    evaluate.add_argument("--run-id", required=True)
    evaluate.add_argument("--idempotency-key", required=True)

    report = commands.add_parser("report")
    report.add_argument("--project-id", required=True)
    report.add_argument("--report-id", required=True)
    return parser


def _dispatch(client: HrmExperimentsClient, args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "capabilities":
        return client.request("GET", "capabilities")
    if args.command == "preflight":
        return client.request("POST", "preflight", body=_load_json(args.request))
    if args.command.endswith("-list"):
        resource = {"datasets-list": "datasets", "checkpoints-list": "checkpoints", "runs-list": "runs"}[args.command]
        return client.request(
            "GET",
            resource,
            query={"project_id": args.project_id, "cursor": args.cursor, "limit": args.limit},
        )
    if args.command in {"dataset-register", "checkpoint-admit", "run-start"}:
        resource = {"dataset-register": "datasets", "checkpoint-admit": "checkpoints", "run-start": "runs"}[args.command]
        return client.request(
            "POST",
            resource,
            body=_load_json(args.request),
            idempotency_key=args.idempotency_key,
        )
    if args.command == "run-status":
        return client.request(
            "GET", f"runs/{args.run_id}", query={"project_id": args.project_id}
        )
    if args.command == "run-events":
        return client.request(
            "GET",
            f"runs/{args.run_id}/events",
            query={"project_id": args.project_id, "cursor": args.cursor, "limit": args.limit},
        )
    if args.command == "run-cancel":
        return client.request(
            "POST",
            f"runs/{args.run_id}/cancel",
            query={"project_id": args.project_id},
            body=_load_json(args.request),
            idempotency_key=args.idempotency_key,
        )
    if args.command == "evaluate":
        return client.request(
            "POST",
            "evaluations",
            body={"project_id": args.project_id, "run_id": args.run_id},
            idempotency_key=args.idempotency_key,
        )
    if args.command == "report":
        return client.request(
            "GET", f"reports/{args.report_id}", query={"project_id": args.project_id}
        )
    raise ClientError("command_unsupported")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        client = HrmExperimentsClient(
            args.base_url,
            _token(args.token_file),
            allow_http=args.allow_http,
            timeout_seconds=args.timeout,
        )
        result = _dispatch(client, args)
    except ClientError as exc:
        print(json.dumps({"ok": False, "reason_code": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
