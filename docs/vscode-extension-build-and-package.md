# VS Code Extension Build and Package (VSC-T03)

Extension root: `client_surfaces/vscode_extension`

## Build and test

```bash
cd client_surfaces/vscode_extension
npm ci
npm run lint
npm run test
npm run test:smoke
npm run compile
```

## VSIX packaging

```bash
cd client_surfaces/vscode_extension
npm run package
```

Expected artifact path:

`client_surfaces/vscode_extension/dist/ananta-vscode-extension.vsix`

Build or test failures block `runtime_mvp` claims for this surface.

## CI lane

`quality-and-docs` includes a dedicated `vscode-extension` job that runs `npm ci`, lint, test, smoke test, compile, and publishes the VSIX artifact.
