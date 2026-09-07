# Meet source profile and threat model (MAP-03)

## Source audit and planned additive binding

The dialog assignment v1 handler executes a fixed, operator-installed Worker
runtime. `screen.publish` opens only `OwnedDialogScreen`: a new offline context,
not a desktop picker or an arbitrary existing tab. `speech.publish` consumes a
Hub-authorized generated reply. `avatar.publish` uses the neutral generated
canvas, or, only with the existing `avatar_images` negotiation, an immutable
Hub-selected persona image. Neither the request nor a Worker callback may add
an arbitrary source class, profile path, URL, cookie jar or capture device.

Implement a small immutable profile projection shared outside the Hub domain.
The Hub derives it from the closed capability set and existing image option,
persists it with new task dispatches and rechecks it on every authority read.
There is no caller-selected source policy. Existing tasks without this additive
field retain exactly the same fixed v1 handler semantics; a present malformed,
unknown or expanded profile fails closed. Do not add fields to the closed v1
Worker wire or force old Workers to accept a new assignment schema.

| Source class | Trusted origin and restriction |
| --- | --- |
| `agent_browser` | Fixed task-owned offline CDP workspace; no arbitrary Browser-Use/Camofox admission. |
| `generated_audio` | Hub-delegated response plus pinned speech configuration; publication still requires its own current control/Meet grant. |
| `generated_video` | Neutral generated KI canvas in the dialog profile; clip/render profiles are separate execution paths. |
| `persona_image` | Only the image-negotiated dialog variant; current Hub profile revision and scoped immutable asset, not identity or permission. |
| `human_device_capture` | Not supported by this profile: no getUserMedia/getDisplayMedia, personal profile, host display or device mount. Receiving an independently consented publication is not capturing a human device. |

This is a trusted installed-code/profile boundary, not remote attestation of a
compromised Worker. A malicious endpoint can lie about bytes; source labels alone
must never certify provenance or satisfy a release gate. Full profile unification
across the separate one-shot clip/render/browser-task paths remains MAP-03 work.

## Independent rights

Chat read, chat send, audio receive, screen publish, speech publish and avatar
publish are independent closed capabilities. No record, model-training, tool
execution or human-capture operation exists in the dialog capability contract.
An admission grant, persona image or display name cannot add one. Audio receipt
also needs Meet's publication-specific current receive permission and a separate
Hub audio child task. Audio bytes are bounded transient processing input, not
authorization to retain recordings or train a model. A future retention/training
adapter needs its own explicit policy and contract; it cannot reuse receive.

## Concrete negative cases and existing enforcement seams

| Threat | Required denial / source seam |
| --- | --- |
| Cross-tenant/project asset or session | Exact scope in `MeetDialogAuthority`, `MeetPersonaProfiles`, `MeetAvatarProfiles`, image/voice hydration and current Meet backchannel; no alternate-tenant fallback. |
| URL/SSRF or cookie/profile capture | Canonical pinned Meet origin, bounded no-redirect/no-proxy model HTTP; offline screen routing; separate browser-task admission denies unsupported live source before actions. |
| Prompt injection from chat/transcript/media | Untrusted bounded event data enters Hub admission, never tools, policy, grants or raw orchestration; model output stays data and cannot choose the next tool/worker. Broader multimodal/context hardening remains MAP-27. |
| Compromised participant | Meet session/device proof and exact source receive consent remain independent of Hub sender policy. Presence alone never enables listening or remote browser control. |
| Replay / stale generation / lost Worker | Signed request/response domains, persisted dispatch fence, current task CAS, exact runtime/membership/source generation, short browser freshness watchdog and original-deadline reconciliation. No self-renewal. |
| Signing-key loss or unknown issuer | Machine admission fails with pinned issuer verification; no unsigned fallback or copied human cookies. Provisioning/rotation and public deployment remain separate acceptance tasks. |
| Source/profile escalation | Closed request/callback/assignment fields and exact Hub-derived profile equality. Invalid stored projection must fail before renewed authority or Worker dispatch. |
| Secret-bearing navigation / hidden canvas | Existing offline source denies navigation and unknown embedded content; broader frame-race and receiver secret-marker acceptance remains MAP-15, not proved by a DOM-only unit test. |

Tests must automate consent fixtures and all denials with bounded diagnostics.
No human interaction and no production trust or capture opt-in is enabled by
these changes. This threat model does not label outstanding runtime or external
acceptance as complete.

## Structure

Keep pure classification separate from Hub task persistence/authorization (SRP,
DIP). Preserve existing lifecycle, media timing and closed wire behavior. The
already documented broad Hub composition and Worker executor SRP/DIP debt is not
expanded into a new policy scheduler.
