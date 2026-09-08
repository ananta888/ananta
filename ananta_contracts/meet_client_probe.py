"""Closed installed-browser feasibility; never a grant or remote attestation."""

from ananta_contracts.meet_source_profile import CAPABILITIES

PORTS = frozenset({"session", "mp4", "chat", "audio", "screen", "screenAudio", "speech", "avatar"})
CODECS = frozenset({"vp8Send", "vp8Receive", "opusSend", "opusReceive"})
FIELDS = frozenset(
    {"schema", "client", "frameEnvelope", "nativeAdapter", "secureContext", "encodedTransform", "codecs", "ports"}
)


def _booleans(value, fields):
    return type(value) is dict and set(value) == fields and all(type(item) is bool for item in value.values())


def require_client_probe(value, capabilities, *, mp4=False):
    if (
        type(capabilities) not in (list, tuple)
        or len(capabilities) > len(CAPABILITIES)
        or any(type(item) is not str or item not in CAPABILITIES for item in capabilities)
        or len(set(capabilities)) != len(capabilities)
        or type(mp4) is not bool
    ):
        raise ValueError("meet_client_probe_requirements_invalid")
    if (
        type(value) is not dict
        or set(value) != FIELDS
        or value["schema"] != "ananta.meet-client-probe.v1"
        or value["client"] != "isolated-browser-v1"
        or value["frameEnvelope"] != "codec-prefix-v1"
        or value["nativeAdapter"] is not False
        or type(value["secureContext"]) is not bool
        or type(value["encodedTransform"]) is not bool
        or not _booleans(value["ports"], PORTS)
        or not _booleans(value["codecs"], CODECS)
    ):
        raise ValueError("meet_client_probe_invalid")
    ports, codecs = {"session"}, set()
    if mp4:
        ports.add("mp4")
        codecs.update({"vp8Send", "opusSend"})
    for capability in capabilities:
        ports.add(
            {
                "chat.read": "chat",
                "chat.send": "chat",
                "audio.receive": "audio",
                "screen.publish": "screen",
                "speech.publish": "speech",
                "avatar.publish": "avatar",
            }[capability]
        )
        if capability in ("screen.publish", "avatar.publish"):
            codecs.add("vp8Send")
        elif capability == "speech.publish":
            codecs.add("opusSend")
        elif capability == "audio.receive":
            codecs.add("opusReceive")
    if not (
        value["secureContext"]
        and value["encodedTransform"]
        and all(value["ports"][port] for port in ports)
        and all(value["codecs"][codec] for codec in codecs)
    ):
        raise ValueError("meet_client_probe_unsupported")
