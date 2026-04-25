# Native Worker vs Aider / OpenCode / ShellGPT / Copilot CLI

## What Ananta borrows

- **Aider:** patch-oriented workflow and git-aware iteration.
- **OpenCode:** autonomous coding loop concepts with explicit phases.
- **ShellGPT:** command explanation and plan-first shell UX.
- **Copilot CLI:** conversational helper ergonomics for local developer workflows.

## What Ananta does differently

1. Hub-controlled orchestration remains mandatory; adapters never own planning.
2. Policy + approval are first-class execution gates.
3. Artifacts are structured and auditable before mutation.
4. External adapter output is untrusted until parsed and validated.
5. Degraded/denied outcomes are explicit and machine-readable.

## Deliberate non-goals

- No mandatory dependency on commercial tools.
- No direct adapter-to-repository mutation bypass.
- No silent "best effort" execution that hides failures.
