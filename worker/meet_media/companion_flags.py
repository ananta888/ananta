"""Feature flags for the Meet companion avatar and its CodeCompass self-knowledge.

Both flags default to enabled so the existing companion behaviour is
preserved; operators disable them explicitly (``0``/``false``/``off``).
"""

import os

AVATAR_FLAG = "ANANTA_MEET_AVATAR_ENABLED"
CODECOMPASS_FLAG = "ANANTA_MEET_AVATAR_CODECOMPASS"
_FALSE = frozenset({"0", "false", "off", "no"})


def _enabled(name, environ):
    value = str(environ.get(name, "1")).strip().lower()
    return value not in _FALSE


def avatar_enabled(environ=None):
    return _enabled(AVATAR_FLAG, os.environ if environ is None else environ)


def codecompass_enabled(environ=None):
    return _enabled(CODECOMPASS_FLAG, os.environ if environ is None else environ)
