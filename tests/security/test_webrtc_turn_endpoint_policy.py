import json

import pytest

from agent.services.webrtc_turn_endpoint_policy import (
    StaticTurnDnsResolver,
    TurnEndpointRule,
    WebrtcTurnEndpointPolicy,
    WebrtcTurnEndpointPolicyError,
)


def _policy(addresses=("8.8.8.8",), now=None):
    now = now or [1000]
    rule = TurnEndpointRule(
        "turns:turn.example.test:5349", "turns", "turn.example.test", 5349, "tls", "eu-1", "peer",
        ("8.8.8.8",), False, ("pin-a",),
    )
    return WebrtcTurnEndpointPolicy(
        (rule,),
        resolver=StaticTurnDnsResolver({"turn.example.test": addresses}),
        diagnostic_secret=b"p" * 32,
        clock=lambda: now[0],
    )


def test_allowlist_dns_pin_tls_region_consumer_and_redirects_fail_closed():
    policy = _policy()
    assert policy.authorize("turns:turn.example.test:5349", region="eu-1", consumer="peer", observed_tls_spki_sha256="pin-a").allowed
    assert policy.authorize("turns:turn.example.test:5349", region="eu-2", consumer="peer", observed_tls_spki_sha256="pin-a").reason_code == "turn_endpoint_not_allowlisted"
    assert policy.authorize("turns:turn.example.test:5349", region="eu-1", consumer="peer", observed_tls_spki_sha256="wrong").reason_code == "turn_endpoint_tls_certificate_untrusted"
    assert policy.authorize("turns:turn.example.test:5349", region="eu-1", consumer="peer", observed_tls_spki_sha256="pin-a", redirect_count=1).reason_code == "turn_endpoint_redirect_forbidden"
    assert _policy(("169.254.169.254",)).authorize("turns:turn.example.test:5349", region="eu-1", consumer="peer", observed_tls_spki_sha256="pin-a").reason_code == "turn_endpoint_ssrf_blocked"


def test_userinfo_unicode_alternate_ip_and_candidate_sentinel_are_not_exposed():
    now = [1000]
    policy = _policy(now=now)
    for url in (
        "turns:user@turn.example.test:5349",
        "turns:türn.example.test:5349",
        "http://turn.example.test:5349",
    ):
        assert not policy.authorize(url, region="eu-1", consumer="peer").allowed
    candidate = "candidate:1 1 udp 1 10.0.0.7 5000 typ host CANDIDATE-SECRET-SENTINEL"
    redacted = policy.redact_candidate(candidate, scope_id="room-a")
    assert redacted["candidate_class"] == "host"
    assert "10.0.0.7" not in json.dumps(redacted)
    assert "CANDIDATE-SECRET-SENTINEL" not in json.dumps(redacted)
    assert redacted == policy.redact_candidate(candidate, scope_id="room-a")
    assert redacted["diagnostic_ref"] != policy.redact_candidate(candidate, scope_id="room-b")["diagnostic_ref"]
    now[0] = 1900
    assert redacted["diagnostic_ref"] != policy.redact_candidate(candidate, scope_id="room-a")["diagnostic_ref"]
    assert policy.authorize("turns:turn.example.test:5349", region="eu-1", consumer="peer", observed_tls_spki_sha256="pin-a", relay_only=True).candidate_policy == "relay_only_no_host_candidates"


def test_private_allowlist_never_overrides_metadata_loopback_or_link_local_blocks():
    for address in ("169.254.169.254", "127.0.0.1", "::1"):
        host = f"[{address}]" if ":" in address else address
        rule = TurnEndpointRule(
            f"turn:{host}:3478",
            "turn",
            address,
            3478,
            "udp",
            "eu-1",
            "peer",
            (address,),
            True,
        )
        with pytest.raises(WebrtcTurnEndpointPolicyError, match="turn_endpoint_ssrf_blocked"):
            WebrtcTurnEndpointPolicy(
                (rule,),
                resolver=StaticTurnDnsResolver({}),
                diagnostic_secret=b"p" * 32,
            )
