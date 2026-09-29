from __future__ import annotations

import pytest

from agent.services.jmap_endpoint_policy import (
    JmapEndpointPolicy,
    JmapEndpointPolicyConfig,
    JmapEndpointPolicyError,
)


def _public(_host: str, _port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


def test_endpoint_policy_revalidates_related_origins_and_templates() -> None:
    policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(
            external_network_enabled=True,
            allowed_related_origins=("https://api.example.com",),
        ),
        resolver=_public,
    )
    initial = policy.validate_initial("https://mail.example.com/.well-known/jmap")
    api = policy.validate_related(
        "https://api.example.com/jmap",
        trusted_origin=initial.origin,
        purpose="api",
    )
    assert api.origin == "https://api.example.com:443"
    download = policy.expand_template(
        "https://api.example.com/download/{accountId}/{blobId}/{name}?accept={type}",
        variables={
            "accountId": "A1",
            "blobId": "B1",
            "name": "report 1.pdf",
            "type": "application/pdf",
        },
        trusted_origin=initial.origin,
        purpose="download",
    )
    assert "report%201.pdf" in download.url
    assert "application%2Fpdf" in download.url


def test_endpoint_policy_blocks_private_dns_and_unlisted_redirect_origin() -> None:
    private = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=lambda _host, _port: ("127.0.0.1",),
    )
    with pytest.raises(JmapEndpointPolicyError, match="jmap_endpoint_address_forbidden"):
        private.validate_initial("https://mail.example.com/.well-known/jmap")

    policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(external_network_enabled=True),
        resolver=_public,
    )
    initial = policy.validate_initial("https://mail.example.com/.well-known/jmap")
    with pytest.raises(JmapEndpointPolicyError, match="jmap_related_origin_not_allowlisted"):
        policy.validate_redirect(
            "https://attacker.example/session",
            current_url=initial.url,
            trusted_origin=initial.origin,
        )


def test_endpoint_policy_requires_explicit_local_host_and_cidr() -> None:
    policy = JmapEndpointPolicy(
        config=JmapEndpointPolicyConfig(
            local_endpoints_enabled=True,
            allowed_local_hosts=("jmap.test",),
            allowed_local_cidrs=("127.0.0.0/8",),
        ),
        resolver=lambda _host, _port: ("127.0.0.1",),
    )
    endpoint = policy.validate_initial("http://jmap.test/.well-known/jmap")
    assert endpoint.local is True
