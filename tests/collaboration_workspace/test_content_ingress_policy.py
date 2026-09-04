from __future__ import annotations

import hashlib

import pytest

from agent.services.collaboration_content_ingress_policy import CollaborationContentIngressPolicy


def item(kind: str = "markdown", content: str | None = "safe text") -> dict[str, object]:
    encoded = (content or "").encode()
    return {
        "kind": kind,
        "content_id": "content-a",
        "media_type": {
            "url": "text/uri-list",
            "markdown": "text/markdown",
            "html": "text/html",
            "patch": "text/x-diff",
            "attachment": "application/pdf",
            "tool_output": "text/plain",
        }[kind],
        "encoding": "utf-8",
        "size_bytes": 10 if kind == "attachment" else len(encoded),
        "content_digest": hashlib.sha256(encoded).hexdigest() if content is not None else "a" * 64,
        "content": content,
        "source_url": content if kind == "url" else None,
        "relative_path": "changes/update.patch" if kind == "patch" else None,
        "scan_status": "clean",
    }


@pytest.mark.parametrize("kind", ["markdown", "html", "patch", "attachment", "tool_output"])
def test_typed_content_ingress_accepts_only_clean_bounded_data(kind: str) -> None:
    content = None if kind == "attachment" else "safe text"
    assert CollaborationContentIngressPolicy().validate(item(kind, content))["kind"] == kind


def test_url_ingress_requires_public_https_without_credentials() -> None:
    policy = CollaborationContentIngressPolicy()
    assert policy.validate(item("url", "https://docs.example.test/page"))["source_url"].startswith("https://")
    for invalid in ("http://example.test", "https://user:secret@example.test", "https://127.0.0.1/path"):
        with pytest.raises(ValueError, match="url_(invalid|private)"):
            policy.validate(item("url", invalid))


def test_content_ingress_rejects_bad_digest_path_secret_and_inline_attachment() -> None:
    policy = CollaborationContentIngressPolicy()
    with pytest.raises(ValueError, match="digest_or_size_mismatch"):
        policy.validate({**item(), "content_digest": "b" * 64})
    with pytest.raises(ValueError, match="path_invalid"):
        policy.validate({**item("patch", "safe"), "relative_path": "../escape.patch"})
    with pytest.raises(ValueError, match="sensitive_content_rejected"):
        policy.validate(item("tool_output", "Bearer abcdefghijklmnop"))
    with pytest.raises(ValueError, match="inline_content_rejected"):
        policy.validate(item("attachment", "raw bytes"))
