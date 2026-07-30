"""Compatibility facade for shared CodeCompass candidate resolution."""

from ananta_codecompass.candidate_resolver import *  # noqa: F401,F403
from ananta_codecompass.candidate_resolver import (  # noqa: F401
    _classify_path,
    _is_source_path,
    _looks_like_path,
    _source_path_multiplier,
    _stem_boost,
    _stem_tokens,
)
