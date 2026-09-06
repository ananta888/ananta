"""Dedicated voice runtime service for transcription and voice intent extraction."""


def create_app(config=None):
    """Load the web composition only when requested, not for backend imports."""
    from .app import create_app as factory

    return factory(config)


__all__ = ["create_app"]
