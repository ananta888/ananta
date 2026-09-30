"""Resource policy for opaque semantic relay envelopes.

Compatibility re-export: the frozen ``SemanticRelayLimits`` value type lives in
``agent.models.semantic_relay_limits`` so relay persistence adapters can
enforce quotas without depending on the service layer (DIP). The objects are
identical; existing importers keep working unchanged.
"""

from __future__ import annotations

from agent.models.semantic_relay_limits import DEFAULT_SEMANTIC_RELAY_LIMITS, SemanticRelayLimits

__all__ = ["DEFAULT_SEMANTIC_RELAY_LIMITS", "SemanticRelayLimits"]
