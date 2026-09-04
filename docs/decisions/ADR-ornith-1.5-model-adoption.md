# ADR: Ornith 1.5 model adoption

Status: Conditional / production No-Go (2026-09-04)

Ornith integrates through existing model profiles and provider adapters. The
Hub remains the sole owner of catalog, routing, import admission, policy,
evidence and fallback; workers only execute closed assignments. This avoids a
parallel model control plane and protects SRP/DIP boundaries.

9B Q4_K_M is the first RTX-3080 evaluation candidate. 35B-A3B is a distinct
64-GB offload experiment. 397B is explicitly unroutable locally. None becomes
default from upstream benchmark or capability claims.

Production adoption is currently rejected because the audited repositories do
not contain the referenced license text and no exact local runtime, vision,
quality or 30-minute stability run has completed under production evidence.
If those gates later pass, promotion remains profile- and role-specific.
