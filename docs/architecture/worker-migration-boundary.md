# Worker Migration Boundary: Existing Modes → ExecutionEnvelope

**Track:** EW-T006  
**Date:** 2026-05-10

Maps every existing worker mode onto capability classes and defines the migration strategy.  
Existing tests continue to pass through a compatibility adapter that wraps legacy calls into an `ExecutionEnvelope`.

---

## Mode mapping

| Existing mode | Capability class(es) | Approval default | Migration stage |
|---|---|---|---|
| `plan_only` | `planning` | allow | wrap in envelope, no approval required |
| `patch_propose` | `code_read`, `patch_propose` | allow | wrap in envelope |
| `patch_apply` | `code_read`, `patch_propose`, `patch_apply` | `patch_apply` → confirm_required | wrap + require ApprovalRef |
| `command_plan` | `shell_plan` | allow | wrap in envelope |
| `command_execute` | `shell_plan`, `shell_execute` | `shell_execute` → confirm_required | wrap + require ApprovalRef |
| `test_run` | `test_run` | allow | wrap in envelope |
| `verify` | `verify` | allow | wrap in envelope |

---

## Compatibility adapter

Legacy callers that send a bare mode string (without an `ExecutionEnvelope`) are wrapped by `LegacyEnvelopeAdapter`:

```python
# worker/core/legacy_adapter.py (created in EW-T006 migration)
class LegacyEnvelopeAdapter:
    def wrap(self, *, task_id: str, mode: str, context: dict) -> ExecutionEnvelope:
        """Convert a legacy mode call into a minimal ExecutionEnvelope.
        Logs a deprecation warning; downstream code sees only envelopes.
        """
```

The adapter:
- Emits a `DeprecationWarning` log line with the legacy mode name.
- Produces a minimal `ExecutionEnvelope` with only the capability classes required for that mode.
- Sets `cloud_allowed=False` by default.
- Leaves `approval_refs` empty — if the mode requires approval, preflight returns `needs_approval`.

---

## Deprecation stages

| Stage | Trigger | Action |
|---|---|---|
| **Stage 1 (current)** | Any mode call | Wrap via adapter; emit deprecation log |
| **Stage 2** | All callers migrated | Adapter logs ERROR instead of WARNING |
| **Stage 3** | Next major release | Adapter removed; bare mode calls return `invalid_request` |

---

## Backward compatibility guarantee

- All existing tests pass unchanged through the adapter (no test modifications needed for migration).
- Adapter is covered by `tests/test_legacy_envelope_adapter.py` (created in EW-T007).
- Adapter behavior is frozen: it never expands capabilities beyond the minimum required for the mode.
