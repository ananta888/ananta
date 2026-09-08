"""Closed public-only request and content projection, independent of Hub imports."""

import pytest

from ananta_contracts.browser_public_fetch import (
    MAX_DOCUMENT_BYTES,
    PublicFetchTarget,
    decode_document,
    document_result,
    validate_fetch_request,
)


def request(url="https://example.com/docs?q=hello%20world", **changes):
    return {
        "schema": "ananta.browser-public-fetch.v1",
        "url": url,
        "allowed_origins": ["https://example.com"],
        **changes,
    }


def test_exact_origin_and_path_without_broadening_or_mutation():
    value = request()
    copy = validate_fetch_request(value)
    assert copy == value and copy is not value
    copy["allowed_origins"].clear()
    assert value["allowed_origins"] == ["https://example.com"]
    target = PublicFetchTarget.parse(value["url"])
    assert (target.hostname, target.port, target.path) == ("example.com", 443, "/docs?q=hello%20world")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.1",
        "https://127.0.0.1",
        "https://[::1]",
        "https://169.254.169.254",
        "https://[64:ff9b::808:808]",
        "https://example.com:444",
        "https://user:password@example.com",
        "https://example.com/#fragment",
        "https://example.com/%00",
        "https://example.com/%0d%0aHeader:injected",
        "https://example.com/%250a",
        "https://example.com/%zz",
        "https://example.com/%5c",
        "https://example.com/ö",
        "https://example.com/" + "a" * 2048,
        "https://example.com/login",
        "https://example.com/docs?access_token=value",
        "https://example.com/%6fAuth",
    ],
)
def test_invalid_or_sensitive_target_denied(url):
    with pytest.raises(ValueError):
        PublicFetchTarget.parse(url)


@pytest.mark.parametrize(
    "changes",
    [
        {"allowed_origins": []},
        {"allowed_origins": ["https://example.com/"]},
        {"allowed_origins": ["https://EXAMPLE.com"]},
        {"allowed_origins": ["https://example.com:443"]},
        {"allowed_origins": ["https://*.example.com"]},
        {"allowed_origins": ["https://example.com"] * 2},
        {"allowed_origins": ["http://example.com"]},
        {"allowed_origins": "https://example.com"},
        {"url": "https://child.example.com"},
        {"url": "https://evilexample.com"},
        {"schema": "unknown"},
        {"cookie": "not permitted"},
    ],
)
def test_projection_cannot_expand_origin_or_headers(changes):
    with pytest.raises(ValueError):
        validate_fetch_request(request(**changes))


def test_public_ipv6_and_canonical_root_dot():
    assert PublicFetchTarget.parse("https://[2606:4700:4700::1111]/").origin == "https://[2606:4700:4700::1111]"
    assert validate_fetch_request(request("https://EXAMPLE.com.:443/a"))["url"].endswith("/a")


def test_document_roundtrip_is_exact_utf8_and_digest_is_not_evidence_identity():
    content = "<h1>Öffentlich</h1>".encode()
    value = document_result(content)
    assert decode_document(value) == content.decode()
    assert set(value) == {"schema", "content_base64", "sha256"}
    assert len(value["sha256"]) == 64


@pytest.mark.parametrize("content", [b"", b"\xff", b"a" * (MAX_DOCUMENT_BYTES + 1), "not bytes"])
def test_invalid_document_denied(content):
    with pytest.raises(ValueError):
        document_result(content)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "unknown"},
        {"sha256": "0" * 64},
        {"content_base64": "%%%%"},
        {"content_base64": " YQ=="},
        {"content_base64": "YR=="},
        {"html": "extra"},
    ],
)
def test_worker_document_wire_is_closed_and_canonical(changes):
    with pytest.raises(ValueError, match="browser_public_document_invalid"):
        decode_document({**document_result(b"a"), **changes})
