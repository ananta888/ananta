# Optional image-backed Meet turns

The existing `/api/meet/v1/projects/{project}/turns` (and task-bound variant)
accepts an optional `persona_image_id`. It names an already admitted active
persona image in the same tenant/project; it is not a path, URL, image upload,
persona identity or Meet room grant. Requests without it retain the procedural
neutral AI avatar. Existing clients and the bounded MP4 publication path remain
compatible.

Example request shape (replace the descriptive placeholder with an actual
admitted artifact identifier):

```json
{
  "text": "Begrüße das Team kurz auf Deutsch.",
  "persona_image_id": "admitted-image-artifact-id",
  "publish_to_meet": false
}
```

## Separate rights and immutable binding

Both optional persona-image admission and Meet media composition must be enabled.
`MeetPersonaImages` is a narrow adapter around the existing asset service. It
checks current project/policy/source/run/asset authority and hashes before loading
the normalized image. Local generated-video preview uses the **preview** purpose.
`publish_to_meet=true` additionally requires the asset's **publish** purpose and
the existing independently issued room admission grant. Neither right implies
the other. Denials are bounded `meet_persona_image_denied_or_unavailable` errors,
not raw storage exceptions or interactive approvals.

The closed worker assignment carries the immutable image reference (tenant,
project, artifact, revision, hash, kind and classification) plus normalized PNG.
The worker validates scope, hash, normalized header/dimensions and static format.
Its internal request budget is now 7 MiB; user API requests remain small because
users send only an artifact ID. Shared private HTTP serving bounds connection
slots, headers, body reads and total connection lifetime.

The Hub task stores **only the reference and chosen purpose**, never the PNG.
Its read-only lease callback rechecks that exact active asset and current policy.
The worker requires that callback before generation, before frame emission and
around encoding; the existing publisher continues checking it during playback.
The Hub rechecks authorization and reference equality before delivering the
result. Revocation prevents delivery of a completed-but-no-longer-authorized
result and stops an active publisher through the existing lease check.

## Rendering and limits

`persona_video` renders the selected static image into a 256×256 RGB frame with
a visible `ANANTA | AI` label (`TEST` is added for test-only assets). No caption
HTML, arbitrary organization text, camera, microphone, display capture or human
browser profile is used. Image preparation and the neutral procedural face are
separate frame sources; `video_frames.encode_frames` owns bounded frame writing
and encoding (SRP/OCP). There is no shared persona frame cache between turns.

Video remains 12 FPS, H.264 NVENC, at most 40 seconds, with a 30-second encoding
timeout and the enclosing turn deadline. The frame sequence is generated before
the existing MP4 publisher starts: this is not a low-latency live image-switch
protocol. During encoding, revoked work may continue until the bounded encoder
returns, but its output cannot pass the subsequent authorization checkpoint.
The result identifies `persona-image-h264_nvenc` and echoes the exact image
reference; a neutral or mismatched result cannot satisfy an image assignment.

## Verified scope and remaining integration

The combined Meet/persona/chat/task/wire suite passed **251 tests in 74.17 seconds**.
Tests cover preview-vs-publication policy, mutated scope/hash/classification,
revocation during work and through a real Hub task lease, content-free persistence,
frame bounds and cancellation, plus existing Meet/chat compatibility.

Private media worker image
`sha256:dc7ac098f0853fca55e5ae23dd20da227927fa056ee6c215209fd78773b7b076`
passed all four local GPU gates in **24.32 seconds**: Whisper ASR, Qwen/Piper/NVENC
response, bounded chat response and static-persona NVENC encoding with synthetic
pixels/silence. The image is the Docker-reported running image identity, not a
Hub `SRC_*`/`RUN_*`. These are technical observations, not production evidence,
proof of a live image-backed public call or generative talking-head quality.
The public Hub/Meet/Caddy deployment was not changed.

This implements per-turn image selection, not MAP-20's organization/team/agent
profile dispatch or hot-switch lifecycle. Independent avatar/browser sources,
multi-generation sessions and public receive/chat/screen gates still depend on
the Meet companion track. Read-only source inspection at Meet `1c1d5b2` still
finds MDS-01 through MDS-09 open; unrelated Broadcast work is not substituted for
those contracts.
