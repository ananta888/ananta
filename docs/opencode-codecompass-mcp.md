# Task-scoped CodeCompass for OpenCode CLI

OpenCode subprocess invocations receive an `ananta_codecompass` remote MCP
entry alongside their existing provider configuration. This also works for
native providers, which do not otherwise need generated provider settings.
The local structured-answer agent keeps its built-in shell/write tools
disabled while allowing CodeCompass reads.

The adapter implements MCP initialize, tools/list, tools/call and ping over
loopback Streamable HTTP. OpenCode's remote-server configuration is documented
at <https://opencode.ai/docs/mcp-servers/>. The installed OpenCode 1.14.18 client
is covered by a real connection test.

Each invocation owns a listener and random bearer secret. The secret is passed
only in `OPENCODE_CONFIG_CONTENT`, included in process-output redaction, and
never written into provider config files or the workspace. The listener closes
when execution completes, raises, times out or is cancelled. The Hub capability
stays in the parent process. HTTP handlers receive a snapshot of the trusted
task context, including the task ID when capability issuance failed.

Discovery lists registered read-only CodeCompass tools. It does not grant index
access. Calls pass the existing tool policy and executor; local execution also
requires an unexpired capability bound to the task and requested operation.
The existing Hub gateway remains authoritative for Hub-routed Worker calls.
Explicit off, scope/role denial, missing capability, missing index and rejected
model-supplied authority remain coded failures. Generic invocation without a
task ID can discover tools but cannot execute them.

Task-bound native-server and live-terminal invocations use the governed
subprocess path. Native session IDs are passed with `--session`; terminal
output is appended to the existing terminal session. Generic interactive
native-server/live-terminal turns without a delegated task retain their
existing transports and do not receive this task-scoped adapter.

SRP separates the HTTP transport from the CodeCompass protocol adapter and
process lifecycle. DIP retains `CliBackendContext` as the service boundary.
The existing long runtime dispatcher and service-locator coupling are preserved;
this change does not introduce a worker orchestration loop or a new issuer.

Focused regression coverage lives in
`tests/cli_backends/test_opencode_codecompass_mcp.py`. These are synthetic
technical observations, not Hub-issued production release evidence.
