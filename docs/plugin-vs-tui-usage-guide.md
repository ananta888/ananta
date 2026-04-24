# When to Use Plugin vs TUI

## Plugin first (Neovim)

Use the plugin when work is code-centric:

- analyze/review while editing
- patch planning from current file/selection
- developer goal submission from editor context
- quick coding workflow loops

## TUI first

Use the TUI when work is operations-centric:

- task/artifact monitoring
- approval queue and decisions
- audit/trace review
- KRITIS and repair session oversight
- runtime/provider diagnostics

## Browser fallback

Use browser views when deep configuration or large detailed screens are needed.

## Rule of thumb

If the task starts in source code, start in plugin.
If it starts in operations/governance, start in TUI.

## Current delivery status

- TUI runtime MVP: available.
- Neovim runtime MVP: available.
- Vim compatibility: deferred until Neovim runtime baseline is stable.
- Eclipse runtime MVP: available (command/view runtime + hardening/CI gates).
