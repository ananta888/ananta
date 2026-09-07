"""Independent video paging scope: image handles cannot page private clips."""

import time

from agent.repositories.persona_asset_cursors import SqlPersonaAssetCursors, cursor_tables

_tables = cursor_tables("video")


class SqlPersonaVideoCursors(SqlPersonaAssetCursors):
    def __init__(self, engine, *, clock=time.time):
        super().__init__(engine, tables=_tables, kind="video", clock=clock)
