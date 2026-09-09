"""Pure receive-mode requirements shared by Hub authority and Worker ingress."""


def audio_mode_permitted(mode, capabilities):
    """Recognition does not require or confer permission to publish a reply."""
    if not isinstance(mode, str):
        return False
    if mode == "off":
        return True
    if mode == "transcribe":
        return "audio.receive" in capabilities
    if mode == "dialog":
        return "audio.receive" in capabilities and "chat.send" in capabilities
    return False
