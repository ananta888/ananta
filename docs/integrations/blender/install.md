# Install

1. Build the addon package:
   `python scripts/build_blender_addon_package.py`
2. Open Blender > Preferences > Add-ons > Install.
3. Select `ci-artifacts/domain-runtime/ananta-blender-addon.zip`.
4. Configure endpoint, profile and token in addon settings or environment variables.

Use `python scripts/run_blender_install_smoke.py` to verify package contents. If a `blender` binary is unavailable, the smoke reports a documented skip for the real background-load step.
