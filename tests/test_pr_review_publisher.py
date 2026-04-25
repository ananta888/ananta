from __future__ import annotations

from unittest.mock import Mock

import pytest

from agent.services.pr_review_publisher import PRReviewPublishRequest, PRReviewPublisher


def test_pr_review_publisher_blocks_outbound_when_policy_denies() -> None:
    publisher = PRReviewPublisher(provider_client=Mock())
    request = PRReviewPublishRequest(
        provider="github",
        repository="org/repo",
        pull_request_number=12,
        summary="Looks good.",
        findings=[],
        policy_allows_outbound=False,
        dry_run=False,
    )
    with pytest.raises(PermissionError, match="outbound_integration_not_allowed"):
        publisher.publish(request)


def test_pr_review_publisher_supports_dry_run_artifact_only_mode() -> None:
    provider_client = Mock()
    publisher = PRReviewPublisher(provider_client=provider_client)
    request = PRReviewPublishRequest(
        provider="github",
        repository="org/repo",
        pull_request_number=13,
        summary="Safe summary.",
        findings=["No blocking issues."],
        policy_allows_outbound=False,
        dry_run=True,
    )
    result = publisher.publish(request)
    assert result["status"] == "artifact_only"
    assert result["outbound_posted"] is False
    assert result["artifact"]["type"] == "review_artifact"
    provider_client.post_comment.assert_not_called()


def test_pr_review_publisher_posts_sanitized_comment_with_mocked_provider_client() -> None:
    provider_client = Mock()
    provider_client.post_comment.return_value = {"id": 101}
    publisher = PRReviewPublisher(provider_client=provider_client)
    request = PRReviewPublishRequest(
        provider="github",
        repository="org/repo",
        pull_request_number=14,
        summary="Summary token=abcd1234\nprompt: never expose this raw prompt",
        findings=["logs: very detailed internal stack", "Keep unit coverage."],
        policy_allows_outbound=True,
        dry_run=False,
    )
    result = publisher.publish(request)
    assert result["status"] == "published"
    assert result["outbound_posted"] is True

    body = provider_client.post_comment.call_args.kwargs["body"]
    assert "prompt:" not in body.lower()
    assert "logs:" not in body.lower()
    assert "token=***" in body
    assert "Keep unit coverage." in body

