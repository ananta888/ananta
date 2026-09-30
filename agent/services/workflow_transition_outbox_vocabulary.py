"""Stable vocabulary of the Hub workflow transition outbox contract.

Schema identifiers, transition/effect kinds, lifecycle states and the
supported runtimes are persisted verbatim; they are value constants only.
"""

from __future__ import annotations

WORKFLOW_TRANSITION_SCHEMA = "ananta.workflow-transition.v1"
WORKFLOW_TRANSITION_EFFECT_SCHEMA = "ananta.workflow-transition-effect.v1"
WORKFLOW_TRANSITION_EFFECT_RESULT_SCHEMA = "ananta.workflow-transition-effect-result.v1"

TRANSITION_KIND_START = "start"
TRANSITION_KIND_ADVANCE = "advance"
TRANSITION_KIND_COMMAND = "command"
TRANSITION_KINDS = frozenset(
    {
        TRANSITION_KIND_START,
        TRANSITION_KIND_ADVANCE,
        TRANSITION_KIND_COMMAND,
    }
)

TRANSITION_STATE_READY = "ready"
TRANSITION_STATE_APPLYING = "applying"
TRANSITION_STATE_COMPLETED = "completed"
TRANSITION_STATE_QUARANTINED = "quarantined"
TRANSITION_STATE_REJECTED = "rejected"
TRANSITION_STATES = frozenset(
    {
        TRANSITION_STATE_READY,
        TRANSITION_STATE_APPLYING,
        TRANSITION_STATE_COMPLETED,
        TRANSITION_STATE_QUARANTINED,
        TRANSITION_STATE_REJECTED,
    }
)
TRANSITION_TERMINAL_STATES = frozenset(
    {
        TRANSITION_STATE_COMPLETED,
        TRANSITION_STATE_QUARANTINED,
        TRANSITION_STATE_REJECTED,
    }
)

EFFECT_STATE_PLANNED = "planned"
EFFECT_STATE_APPLYING = "applying"
EFFECT_STATE_APPLIED = "applied"
EFFECT_STATE_REJECTED = "rejected"
EFFECT_STATES = frozenset(
    {
        EFFECT_STATE_PLANNED,
        EFFECT_STATE_APPLYING,
        EFFECT_STATE_APPLIED,
        EFFECT_STATE_REJECTED,
    }
)

EFFECT_EVENT_APPEND = "event_append"
EFFECT_OWNERSHIP_RESERVE = "ownership_reserve"
EFFECT_AUTHORIZATION_GRANT = "authorization_grant"
EFFECT_SIDE_EFFECT_AUTHORIZE = "side_effect_authorize"
EFFECT_QUEUE_RESERVE = "queue_reserve"
EFFECT_CHECKPOINT_SAVE = "checkpoint_save"
EFFECT_QUEUE_ACTIVATE = "queue_activate"
EFFECT_BINDING_FINALIZE = "binding_finalize"
TRANSITION_EFFECT_KINDS = frozenset(
    {
        EFFECT_EVENT_APPEND,
        EFFECT_OWNERSHIP_RESERVE,
        EFFECT_AUTHORIZATION_GRANT,
        EFFECT_SIDE_EFFECT_AUTHORIZE,
        EFFECT_QUEUE_RESERVE,
        EFFECT_CHECKPOINT_SAVE,
        EFFECT_QUEUE_ACTIVATE,
        EFFECT_BINDING_FINALIZE,
    }
)

TRANSITION_RUNTIME_NATIVE = "ananta-native"
TRANSITION_RUNTIME_LANGGRAPH = "langgraph"
TRANSITION_RUNTIMES = frozenset({TRANSITION_RUNTIME_NATIVE, TRANSITION_RUNTIME_LANGGRAPH})
