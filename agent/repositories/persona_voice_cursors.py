"""Independent expiring voice discovery handles, bound to tenant/project/subject."""

import time

from agent.repositories.persona_asset_cursors import SqlPersonaAssetCursors, cursor_tables

_tables = cursor_tables("voice")


def create_voice_cursors(engine, *, clock=time.time):
    return SqlPersonaAssetCursors(engine, tables=_tables, kind="voice", clock=clock)
