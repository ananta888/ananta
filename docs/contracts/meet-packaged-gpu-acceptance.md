# Explicit packaged Worker GPU acceptance (MAP-10/30)

## Source audit before implementation

At `dc4bac4f3`, the complete locked Worker image builds and passes actual
inventory/liveness checks. The real dialog GPU fixture still derives its image
from the installed service and overlays current Worker/contract source. That
is useful current-source inference coverage, but cannot verify the new image's
packaged inference code. Replacing the installed service just to select a test
image would unnecessarily couple acceptance to deployment.

Add an optional immutable packaged-image selection to the owned GPU fixture.
Only an exact locally installed `sha256:` identity is accepted; no tags, pulls
or implicit fallback. In packaged mode omit both source mounts. Keep the same
read-only model/allowlisted driver/ephemeral-key mounts, internal test network,
non-root execution, resource bounds and owned-resource cleanup. Inspect only
the existing service's driver bindings, never its secrets or environment.
The default current-source path remains unchanged.

The component and browser gate entry points may read an explicit test-only
environment selection and pass it into the fixture. Record the resolved image
identity and packaged/current-source distinction alongside observations.
Do not put environment lookup or test-image selection into production code.
Test malformed/missing/mismatched identities, no fallback or preflight mutation,
source-mount omission, default compatibility and cleanup after partial setup.
Then run the real Qwen/Piper-CUDA/NVENC component and selected-voice browser
gate serially against the freshly built image, with unchanged time budgets.

SRP/DIP: test setup owns image selection through an explicit constructor input;
the production Worker still executes only Hub-bound assignments. Existing
fixture composition remains shared for provider/network/cleanup rather than
duplicating a second GPU environment. Real inference with synthetic policy is
a local technical observation, not public TURN, production deployment or
Registry-backed release evidence.
