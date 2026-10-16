"""Lazy, Hub-only composition root for the opt-in development service."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from flask import current_app

_LOCK = threading.Lock()


def get_development_coordinator():
    from agent.config import settings
    if settings.role != 'hub':
        return None
    filename = os.environ.get('ANANTA_SELF_DEVELOPMENT_POLICY_FILE')
    if not filename:
        return None
    with _LOCK:
        if 'self_development' not in current_app.extensions:
            from agent.services.self_development.coordinator import DevelopmentCoordinator
            from agent.services.self_development.policy import DevelopmentPolicy
            current_app.extensions['self_development'] = DevelopmentCoordinator(DevelopmentPolicy(Path(filename)))
        return current_app.extensions['self_development']


def development_thread_diagnostics():
    """Admin diagnostics: code locations only, never locals or secret values."""
    import sys
    import traceback
    frames = sys._current_frames()
    return [{'thread': thread.name, 'stack': [
        {'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
        for frame in traceback.extract_stack(frames[thread.ident], limit=16)]}
        for thread in threading.enumerate() if thread.ident in frames
        and ('autonom' in thread.name or 'ThreadPoolExecutor' in thread.name)]
