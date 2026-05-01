# Ananta Eclipse Plugin Troubleshooting

## Plugin appears installed but no Ananta views

1. Open **Window -> Show View -> Other...** and search `Ananta`.
2. If missing, restart Eclipse with `-clean`.
3. Verify installation source (correct update site / feature).

## "Already installed" but runtime appears empty

- Open **Window -> Preferences -> Ananta** and verify Hub configuration.
- Run **Test Connection**.
- Confirm token/auth mode is valid.

## p2 install dependency collection errors

- Disable **Contact all update sites during install to find required software**.
- Retry with only the intended repositories enabled.
- Clear stale p2 cache/profile artifacts if the target install is reused.

## Unauthorized / policy denied

- Check auth token in Preferences.
- Check Hub policy/capability responses.
- Use **Ananta Policy and Browser Fallback** for governance links.

## Docker golden path fails

- Ensure Docker daemon is available.
- Re-run with:
  `python3 scripts/run_eclipse_ui_golden_path.py --docker`
- Inspect:
  `ci-artifacts/eclipse/eclipse-ui-golden-path-report.json`
- If `ui_availability_verifier` fails while p2 install succeeds, treat as runtime blocker and keep runtime_complete disabled.

## Security notes

- Do not place tokens in plaintext config files.
- Use Eclipse secure storage integration only.
