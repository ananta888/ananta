# Ananta VS Code Extension (Runtime Bootstrap)

This extension is a thin client for the Ananta backend.  
It does not duplicate orchestration, approval, governance, or repair logic.

## Runtime commands

- `Ananta: Check Backend Health`
- `Ananta: Submit Goal` (quick mode picker for goal/patch/new/evolve)
- `Ananta: Analyze Selection`
- `Ananta: Review File`
- `Ananta: Patch Plan`
- `Ananta: Project New`
- `Ananta: Project Evolve`
- `Ananta: Refresh Sidebar Data`
- `Ananta: Filter Goals/Tasks by Status`

Commands are capability-gated using `/capabilities` handshake and rechecked at execution time.
Editor context payloads are bounded, previewed, and explicitly confirmed before send.

## Sidebar views

- **Status**: connection/capability diagnostics.
- **Goals & Tasks**: list + status filter + detail panel.
- **Artifacts**: list + read-only detail or browser fallback for binary/rich artifacts.
- **Approvals**: pending queue + explicit approve/reject actions (backend-permission aware).
- **Runtime**: summary counts + active filter + refresh diagnostics.

## Development

```bash
cd client_surfaces/vscode_extension
npm ci
npm run lint
npm run test
npm run compile
```

## Package (VSIX)

```bash
cd client_surfaces/vscode_extension
npm run package
```

The VSIX output path is:

`client_surfaces/vscode_extension/dist/ananta-vscode-extension.vsix`
