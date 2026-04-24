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
6. `views/statusTreeProvider.ts`
   - Read-only status surface for connection/capability diagnostics.

## API reuse policy

Runtime features must use existing backend endpoints/read models (health, capabilities, goals/tasks/artifacts, approvals, audit, repair, config-read).
No local fake data model may be used to claim runtime completion.

## SOLID-oriented notes

1. SRP: config, secret handling, transport/client and view rendering are separated.
2. OCP: backend client can be extended with additional methods without rewriting activation flow.
3. DIP: backend client depends on transport abstraction (`HttpTransport`) for testability.

## Safety constraints

1. No client-side bypass for policy/approval.
2. No implicit execution paths.
3. Sensitive values are redacted before output/log rendering.
