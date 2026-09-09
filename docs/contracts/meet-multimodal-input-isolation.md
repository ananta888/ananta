# Meeting input is data, never authority

MAP-27 source audit at 578d44516 / Meet a98706f identifies existing hard
boundaries, not a need for an instruction-filtering prompt or new Worker tools.

The local LLM receives exactly the fixed system prompt and the current input as
one user message, with no history, tool registry, repository context or cloud
fallback. Its result is bounded text. Hub reply dispatch creates an ordinary
`meet_media_turn` with fixed title/description and content-free execution
metadata; the input is not persisted as future planning instructions.
Native current-authority checks fence tenant/project/task/runtime/lease,
membership, policy and generation before dispatch and after generation.

ASR text reuses this bounded reply path only after current source/child/worker
and organization checks. The event's sender comes from the Hub-reserved audio
job, not a spoken claim. Its `human` classification relies on the closed current
Meet contract: `MachineReceivePolicy` admits only authenticated non-machine
source owners and prunes grants before the Hub authorization snapshot. Check
that premise explicitly, including owner authentication/type changes; do not
silently generalize it to future AI-to-AI consent. Machines cannot grant their
own generated speech back as a new human input under this profile.

Visual reception currently runs bounded JPEG statistics, not OCR or a semantic
vision model. Image metadata/text has no instruction or prompt ingress. The
public-browser view is a sanitized, network-blocked rendering of bounded text,
not a trusted prompt, source-origin credential or tool instruction. New OCR,
vision/context retention or AI-to-AI policies will require new explicit
admission boundaries rather than inheriting this acceptance.

## Automated attack sequences

1. Send system-role delimiters, fake policies/identifiers, cross-tenant requests,
   tool/command requests and persistence instructions through the actual local
   HTTP LLM adapter. Verify the fixed two-message role envelope, unchanged model
   and token budget, no tool invocation/egress path and no previous message in
   the next request. Synthetic model responses are not real model robustness.
2. Carry adversarial ASR text through native source/job admission. Check immutable
   sender/session scope, ephemeral transcript handling, and closed rejection of
   foreign source/organization/worker, stale generation or revoked authority.
3. Exercise malicious chat payloads through actual Hub reservations/dispatches,
   native Task persistence and final authority checks; no text may become a
   future task instruction, tool privilege, source identity or profile change.
4. Encode an instruction in a valid JPEG metadata field and supply adversarial
   visual result fields. Only fixed numeric features may leave the image child;
   malformed or broadened results fail. Reuse existing bounded renderer/network
   and callback-signature manipulation regressions for the other ingress paths.
5. Test Meet source consent and pruning against a forged/machine/unauthenticated
   owner and self input. Scope data changes must invalidate, not upgrade, old
   grants or answer generations.

All tests are bounded, headless and synthetic where appropriate. No hostile
prompt is executed as code. These checks prove enforcement and data separation,
not semantic truth of arbitrary model prose, malicious-Worker hardware
attestation or Registry-grounded production provenance. SRP/DIP: retain separate
policy, admission, transport, numeric analysis and output adapters. Existing
large Hub dialog composition remains SRP debt; Workers gain no orchestration.

## Verification

Eight new actual-loopback HTTP, ASR/reservation, native Hub Task/SQL and JPEG
metadata sequences pass with the file-backed WAL harness in **54.92 s**.
The first run passed seven and failed one assertion that incorrectly required
integer RGB averages; the existing closed numeric contract permits finite
floating-point means. The corrected test preserves finite 0–255 bounds. No
production policy/parser or decoder restriction was weakened.

Five hostile inputs preserve exactly the fixed system/current-user envelope,
fixed model/budget and four expected local HTTP operations across two requests;
the following request contains no previous input. Provider tool-call metadata
does not become an action or escape the bounded answer object. ASR spoof text
cannot replace the reserved sender, scope or child and does not enter SQL
receipts. Current source removal invalidates subsequent authority. A real Hub
reply Task retains only its fixed execution metadata, never hostile input or
output as future instructions, and six subsequent scope/generation changes
cannot execute it again. A valid JPEG containing an instruction comment emits
only numeric features; six extra authority/instruction result fields fail.

The combined current-source chat admission/dispatch, HTTP transport, audio
completion, role lifecycle, authenticated callback, browser renderer, visual
and authorization-race matrix passes **292 tests in 107.27 s**. Meet adds three
source-identity attack sequences; together with receive-policy/chat-queue checks
**29 tests pass in 0.175 s** (8f63d1a). Previously admitted owners becoming
machine or unauthenticated immediately lose media eligibility, their grants
are pruned, and restoring attributes cannot resurrect the old consent. Forged
owner objects, role claims, self input and added instruction fields are denied.
Meet's chat view renders both author and message through Angular interpolation,
not HTML or executable instructions.

These boundaries satisfy MAP-27 for the currently implemented stateless local
chat/ASR and numeric visual profile. The independent combined Meet aggregate
at a98706f also passed 874 UI and 876 Node cases, with its documented opt-in
external skips. No semantic model-truth guarantee, future OCR/context/tool
policy, malicious-Worker attestation or production provenance is inferred.
