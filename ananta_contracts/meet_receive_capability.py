"""Source-kind receive ceilings, independent of publication authority."""


def receive_capability(source):
    if source in ("microphone", "screen-audio"):
        return "audio.receive"
    if source in ("camera", "screen"):
        return "video.receive"
    return None
