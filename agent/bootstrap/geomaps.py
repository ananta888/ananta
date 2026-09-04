"""Composition root for the Hub-owned GeoMap catalog."""

from __future__ import annotations

from flask import Flask

from agent.services.geomaps import GeoMapService


def initialize_geomaps(app: Flask) -> None:
    app.extensions["geomap_service"] = GeoMapService()


__all__ = ["initialize_geomaps"]
