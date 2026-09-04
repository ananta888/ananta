# Qwen3.8 abliterated threat model

A safety-modified community model may request disallowed actions more readily,
misrepresent approval or tool output, follow injected document instructions,
or expose sensitive context. Model refusal is not a control.

The Hub permits only an expiring, exact-revision local research run. Normal
routing, fallbacks, public use, tools, network, writes, secrets and personal
data are denied. The runtime sandbox provides a second independent boundary.
Red-team cases use synthetic data and non-executing tool schemas only.
