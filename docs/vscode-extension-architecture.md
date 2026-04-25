# VS Code Extension Architecture (VSC-T04)

## Architecture intent

The VS Code extension is implemented as a **thin client adapter** over existing Ananta backend APIs.
It must not duplicate hub orchestration or policy logic.

## Module layout

`client_surfaces/vscode_extension/src`:

1. `extension.ts`
   - Activation lifecycle.
   - Command registration and wiring.
   - Status view updates and safe user notifications.
2. `runtime/settings.ts`
   - Reads and validates configuration.
   - Resolves effective runtime profile/auth mode.
3. `runtime/secretStore.ts`
   - SecretStorage wrapper for token handling.
4. `runtime/redaction.ts`
   - Secret-safe logging/error redaction helpers.
5. `runtime/backendClient.ts`
    - Typed backend HTTP client.
    - Timeout handling and degraded-state mapping.
6. `runtime/capabilityGate.ts`
   - Parses capability/permission handshake responses.
   - Evaluates command enablement and execution gates.
7. `runtime/contextCapture.ts`
   - Builds bounded editor-context payloads with warning/block signals.
8. `runtime/resultLinks.ts`
    - Builds result deep-links from backend task/goal/artifact payloads.
9. `runtime/webFallback.ts`
    - Safe browser fallback URL generation from configured base URL.
    - Centralized fallback target labels/path rules.
10. `views/statusTreeProvider.ts`
      - Read-only status surface for connection/capability diagnostics.
11. `views/sidebarProviders.ts`
     - Goals/Tasks, Artifacts, Approvals, Audit, Repair and Runtime sidebar providers.
     - Encodes empty/degraded-state rendering and typed item commands.

## API reuse policy

Runtime features must use existing backend endpoints/read models (health, capabilities, goals/tasks/artifacts, approvals, audit, repair, config-read, goal/analyze/review/patch/project workflows).
No local fake data model may be used to claim runtime completion.

Sidebar actions remain backend-owned:

1. Task/goal/artifact/approval detail is read-only rendering.
2. Approval approve/reject actions call backend approval APIs only.
3. Unsupported artifact/result types degrade to browser fallback.
4. Deep audit and repair execution flows stay fallback-first unless backend-gated.
5. TUI launch is explicit user-triggered terminal integration; secrets are not passed on CLI.

## SOLID-oriented notes

1. SRP: config, secret handling, transport/client, capability gate, context packaging and view rendering are separated.
2. OCP: backend client can be extended with additional methods without rewriting activation flow.
3. DIP: backend client depends on transport abstraction (`HttpTransport`) for testability.

## Safety constraints

1. No client-side bypass for policy/approval.
2. No implicit execution paths.
3. Sensitive values are redacted before output/log rendering.
