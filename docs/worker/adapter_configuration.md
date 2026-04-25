# Worker Adapter Configuration

## Adapter lifecycle classes

- `native`: default worker implementation.
- `optional`: available when local tool is installed.
- `experimental`: disabled by default; explicit opt-in required.
- `unavailable`: tool missing or intentionally disabled.

## Enabling adapters

Adapters are loaded through worker configuration and capability policy.  
Experimental adapters (`copilot_cli`, `opencode`) are never enabled by default.

## Security and governance constraints

1. Adapter responses are treated as untrusted input.
2. Adapter patch output must become `patch_artifact.v1` before apply.
3. Command suggestions must flow through `worker.command.plan` + `worker.command.execute`.
4. Approval-required actions cannot be downgraded by adapter logic.

## Practical notes

- Aider and ShellGPT adapters can be used as optional proposal/planning helpers.
- Copilot CLI requires local user authentication and remains BYO in OSS.
- OpenCode adapter handles unavailable/archived tool states as degraded output.
