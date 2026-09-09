"""Optional browser sample-boundary extension, not receive or reply authority."""


def require_segment_probe(value):
    if (
        value != {"schema": "ananta.meet-audio-segment-probe.v1", "profile": "sample-boundary-v1", "supported": True}
        or type(value.get("supported")) is not bool
    ):
        raise ValueError("meet_audio_segment_port_unavailable")


def require_segment_finished(value, subscription_id, end_sample):
    expected = {
        "schema": "ananta.meet-audio-segment-finished.v1",
        "subscriptionId": subscription_id,
        "endSample": end_sample,
    }
    if value != expected or type(value.get("endSample")) is not int:
        raise ValueError("meet_audio_segment_finish_invalid")
