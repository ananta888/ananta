from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.common.redaction import VisibilityLevel, redact


def _normalize_lines(values: list[str] | None) -> list[str]:
    return [str(item).strip() for item in list(values or []) if str(item).strip()]


def _sanitize_for_comment(value: str) -> str:
    # Reuse central redaction and additionally remove lines that would expose prompt/log internals.
    redacted = str(redact(value, visibility=VisibilityLevel.USER))
    safe_lines = []
    for line in redacted.splitlines():
        lowered = line.strip().lower()
        if lowered.startswith("prompt:") or lowered.startswith("raw_prompt:"):
            continue
        if lowered.startswith("log:") or lowered.startswith("logs:"):
            continue
        safe_lines.append(line)
    return "\n".join(safe_lines).strip()


@dataclass(frozen=True)
class PRReviewPublishRequest:
    provider: str
    repository: str
    pull_request_number: int
    summary: str
    findings: list[str]
    policy_allows_outbound: bool
    dry_run: bool = False


class PRReviewPublisher:
    def __init__(self, provider_client: Any):
        self._provider_client = provider_client

    def _build_comment(self, request: PRReviewPublishRequest) -> str:
        summary = _sanitize_for_comment(request.summary)
        findings = [_sanitize_for_comment(item) for item in _normalize_lines(request.findings)]
        lines = [f"Ananta Review Summary for PR #{request.pull_request_number}", "", summary]
        if findings:
            lines.append("")
            lines.append("Findings:")
            for item in findings:
                lines.append(f"- {item}")
        return "\n".join(lines).strip()

    def _post_comment(self, *, provider: str, repository: str, pull_request_number: int, body: str) -> Any:
        client = self._provider_client
        if hasattr(client, "post_comment"):
            return client.post_comment(
                provider=provider,
                repository=repository,
                pull_request_number=pull_request_number,
                body=body,
            )
        if isinstance(client, Callable):
            return client(
                provider=provider,
                repository=repository,
                pull_request_number=pull_request_number,
                body=body,
            )
        raise TypeError("provider_client_must_support_post_comment")

    def publish(self, request: PRReviewPublishRequest) -> dict[str, Any]:
        if not request.policy_allows_outbound and not request.dry_run:
            raise PermissionError("outbound_integration_not_allowed")

        body = self._build_comment(request)
        provider_result: Any = None
        posted = False
        if request.policy_allows_outbound and not request.dry_run:
            provider_result = self._post_comment(
                provider=request.provider,
                repository=request.repository,
                pull_request_number=int(request.pull_request_number),
                body=body,
            )
            posted = True

        return {
            "schema": "pr_review_publish_result_v1",
            "status": "published" if posted else "artifact_only",
            "provider": request.provider,
            "repository": request.repository,
            "pull_request_number": int(request.pull_request_number),
            "dry_run": bool(request.dry_run),
            "outbound_posted": posted,
            "artifact": {
                "type": "review_artifact",
                "comment_body": body,
                "sanitized": True,
                "provider_result": provider_result if posted else None,
            },
        }

