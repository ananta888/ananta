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
