# avoid-ai-writing integration

Upstream: `conorbronsdon/avoid-ai-writing`

- audited commit: `f9f8265061eaee1004e9ef86383959163dd2477d`
- detector SHA-256: `66cc34590ed17d4b7d7c59323b204e2b231e41ed58502ab89fc9753a64c278f5`
- license SHA-256: `4da9b9f0bb899269b6e79fb383b4c3f24ebcadf7352f970871bae3e215401589`
- runtime: Node.js 18 or newer
- license: MIT, Copyright (c) 2026 Conor Bronsdon

The preferred strategy is a separately provisioned, read-only approved
bundle. Runtime network downloads and moving `main` references are forbidden.
Operators place `detector/patterns.js` in the configured approved bundle and
must match the checksum above.

The detector's 0–100 value is stored as `detector_signal_score`, not Ananta's
overall quality score. `HUMAN_ONLY`, `MIXED` and `AI_ONLY` are diagnostics,
not ground truth and never a rewrite or canary objective. The 44 executable
types, human-readable catalog and LLM-only judgment rules intentionally have
different counts.

English rules are disabled for German profiles unless independently
calibrated. `technical` context reduces expected false positives but does not
replace Ananta's content-kind profiles.
