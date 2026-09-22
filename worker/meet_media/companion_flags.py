"""Feature flags for the Meet companion avatar and its CodeCompass self-knowledge.

The avatar and CodeCompass flags default to enabled so the existing companion
behaviour is preserved; operators disable them explicitly
(``0``/``false``/``off``).

How CodeCompass reaches the model is a separate choice: ``MEET_COMPANION_TOOLS``
(default on) offers it to the model as a callable tool, ``MEET_COMPANION_RAG_PREFIX``
(default off) is the previous behaviour of prepending retrieved snippets to
every routed question. Both may run together, and switching both off leaves
the model ungrounded.
"""

import os

AVATAR_FLAG = "ANANTA_MEET_AVATAR_ENABLED"
CODECOMPASS_FLAG = "ANANTA_MEET_AVATAR_CODECOMPASS"
TOOLS_FLAG = "MEET_COMPANION_TOOLS"
RAG_PREFIX_FLAG = "MEET_COMPANION_RAG_PREFIX"
_FALSE = frozenset({"0", "false", "off", "no"})


def _enabled(name, environ, default="1"):
    value = str(environ.get(name, default)).strip().lower()
    return value not in _FALSE


def avatar_enabled(environ=None):
    return _enabled(AVATAR_FLAG, os.environ if environ is None else environ)


def codecompass_enabled(environ=None):
    return _enabled(CODECOMPASS_FLAG, os.environ if environ is None else environ)


def tools_enabled(environ=None):
    """Offer CodeCompass to the model as a tool it may call (default on)."""
    return _enabled(TOOLS_FLAG, os.environ if environ is None else environ)


def rag_prefix_enabled(environ=None):
    """Prepend retrieved snippets to routed questions, as before (default off)."""
    return _enabled(RAG_PREFIX_FLAG, os.environ if environ is None else environ, default="0")
