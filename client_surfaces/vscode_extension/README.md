# Ananta VS Code Extension (Runtime Bootstrap)

This extension is a thin client for the Ananta backend.  
It does not duplicate orchestration, approval, governance, or repair logic.

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
