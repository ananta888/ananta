"""Strict, content-free observation of Meet's additive integration contract."""

SCHEMA = "ananta.meet-integration.v1"
LEASE = "ananta.meet-session-lease.v1"
CAPABILITIES = (
    "audio.receive",
    "avatar.publish",
    "chat.read",
    "chat.send",
    "screen-audio.publish",
    "screen.publish",
    "speech.publish",
    "video.receive",
)
CONSENT = ("audio.receive", "chat.read", "video.receive")
FIELDS = frozenset(
    (
        "schema",
        "admissionEnabled",
        "supportedCapabilities",
        "operatorCapabilityCeiling",
        "publisherConsentRequired",
        "sessionLease",
    )
)


def _valid(value):
    if not isinstance(value, dict) or value.keys() != FIELDS:
        return False
    ceiling = value["operatorCapabilityCeiling"]
    if not isinstance(ceiling, list) or len(ceiling) > len(CAPABILITIES):
        return False
    if any(not isinstance(item, str) or item not in CAPABILITIES for item in ceiling):
        return False
    return (
        value["schema"] == SCHEMA
        and type(value["admissionEnabled"]) is bool
        and value["supportedCapabilities"] == list(CAPABILITIES)
        and ceiling == sorted(set(ceiling))
        and (not value["admissionEnabled"] or bool(ceiling))
        and value["publisherConsentRequired"] == list(CONSENT)
        and value["sessionLease"] == LEASE
    )


def integration_projection(value, legacy_capabilities):
    """Copy validated metadata only; this observation never grants execution rights."""
    result = {
        "status": "unavailable",
        "schema": None,
        "admission_enabled": None,
        "implemented_capabilities": None,
        "operator_capability_ceiling": None,
        "publisher_consent_required": None,
        "session_lease": None,
    }
    if not _valid(value):
        return result
    legacy = legacy_capabilities if isinstance(legacy_capabilities, dict) else {}
    if (
        legacy.get("schema") != "ananta.meet-capabilities.v1"
        or type(legacy.get("admissionEnabled")) is not bool
        or legacy["admissionEnabled"] != value["admissionEnabled"]
    ):
        result["status"] = "inconsistent"
        return result
    return {
        "status": "observed",
        "schema": SCHEMA,
        "admission_enabled": value["admissionEnabled"],
        "implemented_capabilities": list(CAPABILITIES),
        "operator_capability_ceiling": list(value["operatorCapabilityCeiling"]),
        "publisher_consent_required": list(CONSENT),
        "session_lease": LEASE,
    }
