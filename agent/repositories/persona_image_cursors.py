"""Opaque, short-lived, scope-bound paging handles; never authorization tokens."""

import time

from agent.repositories.persona_asset_cursors import SqlPersonaAssetCursors, cursor_tables

_metadata, scopes, cursors = cursor_tables("image")


class SqlPersonaImageCursors(SqlPersonaAssetCursors):
    def __init__(self, engine, *, clock=time.time):
        super().__init__(engine, tables=(_metadata, scopes, cursors), kind="image", clock=clock)
