# Client Thinness Rules (OSS)

## Purpose

Define strict boundaries for CLI, TUI, Neovim, Eclipse and future Blender/VS Code client surfaces.

Clients are **request/render surfaces**, not orchestration or execution authorities.

## Allowed client responsibilities

- Collect bounded context from user/editor state (selection, file path, project root, bounded related paths).
- Submit requests to Hub APIs.
- Render Hub states (healthy, denied, approval-required, degraded) without local policy shortcuts.
- Keep local profile/runtime UX concerns (auth token handling, redaction, retry hints).

## Forbidden client responsibilities

- No client-side orchestration decisions (routing workers, queue control, task scheduling).
- No client-side policy evaluation or approval decisions.
- No direct tool execution, shell execution or worker execution from client surfaces.
- No direct writes to Hub-owned server state outside public API requests.

## Mandatory behavior

- Denied or degraded states must be surfaced as-is (`policy_denied`, `auth_failed`, `capability_missing`, `backend_unreachable`, etc.).
- Approval-required flows must remain Hub-mediated; clients must not convert them into local execution.
- Context must stay explicit and bounded; unrelated paths and likely secret material are rejected or flagged.
- Surfaces must stay swappable: same thin-client contract for CLI/TUI/Neovim/Eclipse/Blender/VS Code.

## OSS/KRITIS boundary

OSS requires thin-client safety contracts and negative tests.

KRITIS/Enterprise hardening (regulated endpoint controls, signed attestations, tenant governance) is additive and out of scope here.

