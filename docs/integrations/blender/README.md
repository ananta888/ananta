# Blender Integration

The Blender integration is a thin Ananta client surface for hub-governed workflows.

## Runtime scope

- Capture bounded scene context with provenance.
- Submit goals through the hub.
- Read hub tasks, artifacts, approvals and audit/event state.
- Plan exports, renders and scene mutations without executing them implicitly.
- Execute mutating actions only through approval-bound requests.

The hub remains the owner of orchestration, policy, approval, audit and task state.
