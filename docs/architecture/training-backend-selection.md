# Training backend selection

Backend selection is a Hub policy decision over the current Worker capability
probe. Workers only execute the admitted job and cannot register implementations or
delegate to another Worker.

The browser may request a recommendation using objective, method, modality,
resource profile, output format and bounded resource estimates. The response is
advisory and includes alternatives, reasons, capability evidence and estimated
resources. It always requires explicit confirmation and never mutates the selected
backend.

The policy keeps PEFT/TRL and Unsloth selectable, rejects unavailable capabilities
and excludes unmaintained backends from automatic recommendations. Published
upstream benchmarks are not ranking inputs. A backend failure cannot trigger a
hidden mid-attempt fallback; the Hub may create a new auditable attempt instead.

This split protects SRP and dependency inversion: the Hub owns policy through a
small selection service, while each optional trainer implements the same Worker-side
execution port. Heavy trainer dependencies never enter Hub imports.
