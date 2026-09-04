# Ornith 1.5 local runtime runbook

Ornith is default-off. Admit an exact artifact through the Hub import service,
delegate the closed assignment to an import worker, verify it with
`scripts/security/scan_ornith_artifacts.py`, and mount the published cache
read-only. Model startup must never pull files implicitly.

For Ollama, use the values in `deploy/examples/ornith-ollama/.env.example`.
For llama.cpp/LM Studio, bind only a loopback or private-container endpoint and
use the matching model profile. Run CPU contracts first, then resource and
CodeCompass probes under a Hub-reserved evaluation assignment.

Activation progression is `unavailable` → `experimental` → `shadow` →
`opt_in` → `preferred`. Each transition requires source, runtime, capability,
hardware, quality and security evidence for that exact profile. A 9B result
does not promote 35B. `preferred` is never global default by implication.

To roll back, disable the profile and remove its assignment while preserving
the immutable artifact, audit and benchmark history. Existing Phi/Gemma routes
remain unchanged and serve as fallback. Runtime down, model missing, OOM,
context rejection or parser failure returns a bounded reason and routes only
through Hub policy; mutating calls are not replayed automatically.
