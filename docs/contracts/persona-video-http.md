# Private video inspection transport

Video inspection now has a dedicated CPU worker endpoint
`POST /v1/persona-videos` and a read-only Hub callback
`POST /api/persona-media/v1/internal/video-lease`. Neither route creates a
policy or grants publication rights. User-facing upload/bootstrap integration
is a separate lifecycle change; registering the callback does not enable it.

The Hub sends only its closed pre-reserved assignment, bounded MP4 bytes and
media type. Private worker DNS is resolved and pinned before dispatch; the
operator-configured Host header is retained. Proxy inheritance and redirects
are disabled. HMAC domains `persona-video-v1` and `persona-video-lease-v1`
separate video execution and authority checks from each other and from image
messages. Response signatures bind the exact request digest, including the
fresh nonce in every lease check. A video callback rejects user bearer tokens.

Requests have one bounded Content-Length, no Transfer-Encoding, and no
duplicate JSON keys. Input is capped at 3,500,000 bytes, requests at 4,700,000
bytes and results at 2,500,000 bytes. The worker consumes one assignment at a
time and persists consumed lease IDs in its own SQLite replay file before
decoding; restarting the process cannot make an unexpired lease reusable.
Both successful and failed executions consume the lease. At most four HTTP
connections are admitted, with bounded reads and a fixed twenty-second task
deadline. The decoder repeatedly rechecks current Hub authorization, which
must be the literal JSON boolean `true`. No worker can extend the assignment.

The shared inspection executor/HTTP core is configured by immutable image or
video wire profiles, not by caller-selected codecs. The existing image
constructors, endpoint, signing domains, replay table and result schema stay
compatible. SRP/OCP/DIP: orchestration, authority, transport, replay storage and
media normalization remain separate; small composition adapters avoid a
second copy of the security-sensitive image workflow. Existing untyped port
boundaries remain compatibility seams, not broad worker orchestration APIs.

`docker/persona-video/Dockerfile` contains Python and Debian FFmpeg; it installs
no model runtime, browser or CUDA package. The Python base is digest-pinned;
Debian security packages are resolved at build time, so production admission
must bind the actual built image/environment rather than assume a tag is
immutable. `docker-compose.persona-videos.yml` is opt-in, read-only, non-root,
one CPU, 384 MiB, 32 PIDs, no capabilities, no-new-privileges and an internal
network without host ports. The only persistent worker data is its replay
file; the only credential mount is a private inspection key. No public Hub,
Meet instance, Caddy route or trust policy is automatically changed.

Tests in `tests/test_persona_video_http.py` use real HTTP callbacks, Hub Tasks,
Registry identities and video policy persistence, with an explicitly
structural decoder double. They cover completion, revocation during decoding,
cross-domain requests, duplicate/oversized framing and replay after executor
recreation. `tests/test_persona_video_http_container.py` is a separate opt-in
gate against the built `ananta-persona-video:local` image. It creates and
removes only its own disposable container/network, generates synthetic moving
video/audio locally, registers the exact source, and runs actual FFmpeg over
the signed transport under a pre-reserved test-scope Hub run. No human capture,
public deployment or production release evidence is involved.

The combined real CPU-container/HTTP/Hub-task/image-wire/API regression passed
82 tests in 68.28 seconds. The first container setup attempts exposed a Docker
startup/cleanup timeout and a duplicate test-policy binding; both test setup
issues were corrected before the passing run. This remains a synthetic
technical gate, not production admission or end-to-end Meet delivery.

```sh
docker build -f docker/persona-video/Dockerfile -t ananta-persona-video:local .
PERSONA_VIDEO_HTTP_CONTAINER_GATE=1 .venv/bin/python -m pytest \
  tests/test_persona_video_http_container.py -n0 -q --tb=short
```
