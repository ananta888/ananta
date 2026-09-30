"""Map persisted runtime observations to the sources that substantiate a proof."""

from __future__ import annotations

from agent.services.workflow_runtime.release_evidence import RuntimeRunEvidence

# Proofs are accepted only when the persisted observation contains a source
# that can actually demonstrate that proof.  In particular, a non-durable
# runtime may not turn an ephemeral framework checkpoint into production
# checkpoint/recovery evidence.  Native and LangGraph probes therefore emit
# the two Hub-qualified events only after asserting a persisted Hub checkpoint
# can be loaded by a replacement runtime instance.  Temporal may use its
# durable-history events because the record is additionally bound to a durable
# release variant.
_HUB_CHECKPOINT_SOURCE_EVENTS = frozenset(
    {
        "workflow.checkpoint.hub_persisted",
    }
)
_HUB_RECOVERY_SOURCE_EVENTS = frozenset(
    {
        "workflow.checkpoint.hub_restored",
        "workflow.recovery.completed",
    }
)
_DURABLE_CHECKPOINT_SOURCE_EVENTS = frozenset(
    {
        "workflow.checkpoint.created",
    }
)
_DURABLE_RECOVERY_SOURCE_EVENTS = frozenset(
    {
        "workflow.run.resumed",
        "workflow.recovery.completed",
    }
)
_APPROVAL_SOURCE_EVENTS = frozenset(
    {
        "workflow.approval.granted",
        "workflow.run.resumed",
    }
)
_LEDGER_SOURCE_EVENTS = frozenset(
    {
        "workflow.side_effect.completed",
        "workflow.side_effect.failed",
        "workflow.side_effect.uncertain",
    }
)


def _observed_proof_sources(
    record: RuntimeRunEvidence,
    category: str,
) -> tuple[str, ...]:
    """Return persisted observation fields that can substantiate one proof.

    A proof status by itself is intentionally insufficient.  This function is
    also used to render the artifact's ``invariant_evidence`` links, so the
    verifier and an operator can identify the exact command/run that supplied
    a green invariant.
    """

    events = frozenset(str(value) for value in record.observation.event_types)
    if category == "port":
        if not record.observation.terminal_status:
            return ()
        return (
            f"command:{record.command_id}",
            f"terminal:{record.observation.terminal_status}",
        )
    if category == "security":
        return tuple(
            f"policy:{value}"
            for value in sorted(record.observation.policy_decisions)
        )
    if category == "event":
        return tuple(f"event:{value}" for value in sorted(events))
    if category == "artifact":
        return tuple(
            f"artifact:{value}"
            for value in sorted(record.observation.artifact_ids)
        )
    if category == "checkpoint":
        allowed = set(events & _HUB_CHECKPOINT_SOURCE_EVENTS)
        if record.durable:
            allowed.update(events & _DURABLE_CHECKPOINT_SOURCE_EVENTS)
        return tuple(f"event:{value}" for value in sorted(allowed))
    if category == "recovery":
        allowed = set(events & _HUB_RECOVERY_SOURCE_EVENTS)
        if record.durable:
            allowed.update(events & _DURABLE_RECOVERY_SOURCE_EVENTS)
        return tuple(f"event:{value}" for value in sorted(allowed))
    if category == "approval":
        approval_events = events & _APPROVAL_SOURCE_EVENTS
        if not approval_events or not record.observation.gate_ids:
            return ()
        return (
            *(f"event:{value}" for value in sorted(approval_events)),
            *(f"gate:{value}" for value in sorted(record.observation.gate_ids)),
        )
    if category == "ledger":
        ledger_events = events & _LEDGER_SOURCE_EVENTS
        if not ledger_events or not record.observation.side_effect_operations:
            return ()
        return (
            *(f"event:{value}" for value in sorted(ledger_events)),
            *(
                f"operation:{value}"
                for value in sorted(record.observation.side_effect_operations)
            ),
        )
    return ()
