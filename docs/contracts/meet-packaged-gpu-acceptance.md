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

## Implemented fixture and component result

`MEET_DIALOG_GPU_PACKAGED_IMAGE` selects the optional exact local image through
the test entry points. Constructor validation rejects malformed/non-string IDs;
an unsuccessful or mismatched local image inspection cannot fall back or create
resources. Packaged mode reads only driver bindings from the installed service
and omits Worker/shared-contract source mounts. Default current-source behavior
and all existing model/provider/resource/cleanup boundaries are retained.

The combined fixture/observer regression passed 54 tests in 29.19 seconds.
The real packaged component gate passed in 52.26 seconds against
`sha256:3444cb7d1124c52959be70043b4c66fa78bba0e55f109c18b724d2ab46dddb9e`:
Qwen generated 13 output tokens, Piper-CUDA returned 65,792 non-silent PCM
samples and NVENC returned 60,676 video bytes. The report records the selected
image and `packaged_worker=true`; it explicitly denies Hub-dialog, remote
delivery and production-release verification. The browser-level packaged check
is separate and still follows this component result.

The first packaged browser attempt failed in 24.36 seconds before starting
the GPU fixture: the separate, unchanged browser image emitted a generic
sandbox-launch failure. No packaged GPU inference occurred in that attempt.
See `meet-browser-launch-diagnostics.md` for the bounded diagnostic follow-up;
do not replace this failure with the successful component result.

The packaged browser repeat passed in 106.10 seconds after adding only passive
startup diagnostics. It used the exact image above without inference source
mounts. Qwen/Piper produced 205,824 neutral and 113,920 whisper samples; both
answers reached the real receiver with exact text correlation and non-silent
audio (440/218 active observation windows). The chat observer counted exactly
two polled events, ACKs, prepared bindings, spoken callbacks and replies, with
no rejection. Local/remote voice revocation took 854.51/859.21 ms; the separate
screen/chat/parent lifetime and final bounded stop checks remained enabled.
No human capture or production policy/evidence was used.

This demonstrates packaged inference in one successful real private
Hub/Worker/Meet run. The earlier intermittent pre-inference chat and generic
browser-start failures are retained as unresolved; neither was causally fixed
by selecting an image or adding diagnostics. Public/forced-TURN, multi-agent,
long-soak and production rollout criteria are still separate.

The final 44 fixture tests passed in 24.64 seconds, exercising every partial
or uncertain network/provider/Worker setup failure in both current-source and
packaged modes. All owned resources are cleaned in reverse order; missing or
mismatched images fail before resource creation without pulling or fallback.
