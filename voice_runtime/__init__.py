"""Dedicated voice runtime service for transcription and voice intent extraction."""

from .app import create_app

__all__ = ["create_app"]
