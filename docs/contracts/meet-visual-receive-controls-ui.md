# Visual receive controls in the Hub UI

Source audit at ccbb3f648: the Hub and installed Worker support the optional
`video.receive` capability and independent `visual` control, but Angular's
closed dialog response validator rejects both. Consequently an API-created
visual-receive task can invalidate the owner's whole list, including its stop
control. This is an incomplete UI contract, not permission to relax Hub policy.

Add the exact optional capability/control to the existing closed response
adapter and source-control UI. Preserve the old three-source wire format;
unknown capabilities/fields and an unnegotiated visual control still fail.
An explicit default-off start choice requests only visual reception, initially
paused by the Hub. It grants no participant consent, capture, chat, speech,
avatar, screen publication, tool access or automatic source selection in the UI.
The existing Hub admission and bounded visual child choose authorized input.

Use the existing revision-CAS control and terminal stop APIs. Changing another
source must preserve visual state, and changing visual state must preserve all
other controls. Context/identity change clears the local start choice. Explain
bounded image sampling rather than continuous video understanding; visible
controls are optional, not an execution prerequisite.

First reproduce the closed-validator failure; then verify legacy and negative
contracts, HTTP list/stop mapping, explicit start, independent toggles, identity
reset and headless rendered controls. Run the focused Meet frontend batch and
an isolated optimized build without changing the serving frontend output.
This closes a MAP-12 UI omission, not the remaining global-stop/reconnect or
production acceptance. SRP/DIP: the API adapter owns wire validation, the view
owns explicit user choices, and the Hub retains all source authority. The large
existing dialog component remains acknowledged SRP debt; avoid adding policy
or another orchestration loop to it.

## Verification

The current-Hub response reproduction failed before the fix (one failure,
seven negative cases passing, 0.860 s). The exact additive visual capability
and optional control now pass; the view explicitly requests visual-only input
without starting capture, publication, chat or a source-control mutation.
Owner text follows the native owner-filtered list rather than inventing an
identity field. Independent toggles preserve every other negotiated source;
legacy tasks gain no control. Identity/project changes clear the choice.

The focused contract/rendered-UI checks passed 47 cases in 1.47 s. The combined
Meet/auth frontend batch passed 267 cases across 19 files in 2.46 s. It covers
mixed legacy/visual list mapping and terminal DELETE responses, explicit start,
rendered visual pause and whole-task stop, exact CAS payloads, negative schemas,
missing capability, terminal controls and rejected CAS without automatic retry.
An isolated optimized Angular build also passed; serving output and operator
configuration were not changed. The first build invocation selected a nonexistent
`production` configuration and failed before building; the verified command
uses this workspace's `--optimization` option. Existing CommonJS warnings remain.

These are headless UI/technical observations. Real visual receipt/revocation
has its separate installed-Worker tests; neither result grants production trust
or completes all remaining MAP-12 acceptance.
