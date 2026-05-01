# Ananta Eclipse Plugin Operator Runbook

## Build artifacts

- Plugin JAR:
  `client_surfaces/eclipse_runtime/ananta_eclipse_plugin/build/libs/ananta-eclipse-plugin-runtime-0.1.0-bootstrap.jar`
- Update site:
  `ci-artifacts/eclipse/ananta-eclipse-update-site`

## Rebuild update site

```bash
python3 scripts/build_eclipse_update_site.py
```

## Smoke checks

```bash
python3 scripts/smoke_eclipse_runtime_bootstrap.py
python3 scripts/smoke_eclipse_runtime_headless.py
```

## Installed-Eclipse golden path

Host mode:

```bash
python3 scripts/run_eclipse_ui_golden_path.py
```

Docker/Xvfb mode:

```bash
python3 scripts/run_eclipse_ui_golden_path.py --docker
```

Report:

`ci-artifacts/eclipse/eclipse-ui-golden-path-report.json`

## Operational policy

- Eclipse surface is a thin client.
- Hub remains the control plane for orchestration/policy/approvals/audit.
- Do not move workflow ownership into the plugin.

## Release gate notes

- p2 installation and metadata checks must pass.
- UI availability verifier should confirm view registration + openability.
- If runtime_complete is false, keep status at MVP/blocked and publish reason.
