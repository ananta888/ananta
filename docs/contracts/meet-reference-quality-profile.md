# Independent media reference profile and MAP-24 acceptance

This consolidates existing predeclared limits; it does not increase a deadline
after a failed run. The supported reference is local Chromium-owned publishing,
independent persona/screen video and local Qwen/Piper speech on the RTX 3080
(10,240 MiB, observed driver 595.84). Chromium and Firefox receivers are tested.
Firefox publishing without native canvas `requestFrame` remains explicitly
unsupported. This profile is not semantic lip synchronization, receiver A/V
alignment, host-wide GPU arbitration or public-network throughput certification.

| Boundary | Previously declared limit and enforcement |
| --- | --- |
| Independent live clocks | `independent-owned-live-v1`: 500-ms absolute PCM/decoded-video source drift, 750-ms fresh observation/progress; fixed browser monotonic timebase, own generation per source. Failed clocks terminate output. Canvas submission has no invented decoder/RTP timestamp. |
| Hub authority | Original Task/lease deadlines, 2.5-second fresh controls, bounded join/renew/leave and original recovery allowance. Quality observations never refresh authority. |
| Stop and replay | GPU avatar/voice references retain ≤3-second local stop and ≤4-second remote publication disappearance. Other actual interruption/recovery gates retain their own original limits; pending old-generation speech cannot replay. |
| Dialog admission | Hub SQL pool defaults eight sessions, two per publisher, 384-Mbit/s conservative fanout publication reservation; four FIFO waiters and ten-second wait. No actual network shaping is asserted. |
| Generation/model admission | Existing Hub GPU pool single-flight FIFO, immutable model/profile selection, original child deadline; Qwen 1.5B with 2,048 context in the private reference, selected tests at most 64 output tokens and twenty seconds PCM. |
| GPU prerequisites | Native CUDA/driver access and pinned model bytes; at least 4,096 MiB free before private inference setup. Piper's CUDA arena is capped at 2 GiB, not the total host GPU allocation. Foreign jobs cannot be preempted by these tests. |
| Container reference | Each non-GPU dialog publisher: two CPUs, 1 GiB RAM, 256 PIDs; GPU provider/inference fixtures separately have 4 GiB and 256 PIDs. Runtime-image and model mounts are explicit and read-only. |
| Numeric Worker observation | Signed nonce-bound slot/cgroup report, two-second/1-KiB response limit; unavailable counters are null, never a success substitute. Installed CPU reference checks startup/active/terminal samples and ≤2.25 observed interval-average cores (quota plus sampling/burst margin), not continuous peaks. |
| Local encoded clip | Existing fixed 256×256/12-FPS H.264 and 22,050-Hz mono AAC decoder gate: exact video frame count, monotonic clocks, bounded AAC padding and ≤150-ms end-time difference. This is offline clip quality only. |

## Criterion-by-criterion result

1. Separate source lifecycles and common source timebase are implemented through
   the pure contract, browser-owned timeline, bounded Worker timing port and
   exact Hub-negotiated assignment option. Native browser tests received actual
   decoded speech, moving/held/looped video and screens; source drift measured
   147,600 / 144,000 microseconds video and 8,200 microseconds PCM in the initial
   Chromium/Firefox matrix. Stale-source negative tests stopped publication.
2. Generation retirement, stop, renewal and bounded reconnect are verified by
   the installed two-Worker tests, including interrupted old speech, fresh
   consent, same Hub Task and independent surviving publisher. The current
   resource/timing/reconnect reference passed in 65.51 seconds. Earlier actual
   mid-playback pause/cancel tests measured approximately 1.6-second local and
   remote termination, rather than only cancelling a finished clip.
3. Hub generation and dialog pools retain policy/queue ownership; Workers expose
   observations only. Real two-Worker resource samples remained within the
   declared bounds and returned to zero occupied slots. SQL tests cover FIFO,
   changed policy, cross-Hub profile disagreement, stale/duplicate reservations,
   uncertain dispatch quarantine and exact assignment/role rechecks.
4. Fixed deadlines and source/decoder quality bounds are exercised with actual
   installed GPU inference and real receiver output, not merely API receipts.
   At Ananta `4595543df` / Meet `91d9203`, immutable Worker `6a2ac86f9209`:
   `gpu-avatar` passed in 85.434 seconds, with 58,624 real Piper samples and
   local/remote avatar pause 906.42 / 976.56 ms. `gpu-voices` passed in 94.686
   seconds, with 43,776 neutral and 119,552 whisper samples, exactly two
   correlated events/ACKs/prepared/spoken/replies and no chat failures; local/
   remote revocation was 583.96 / 595.74 ms. Both explicitly negotiated the
   timing mode, decoded non-silent receiver PCM, preserved moving owned screen
   output and reported zero capture/transform errors. The separately passed
   component reference exercised actual Qwen/Piper-CUDA/NVENC clip generation.

These current reference runs were reserved by the Hub Registry before execution
and completed with unchanged selected source/build bindings. All remain
synthetic-policy TEST evidence and are rejected for production release. GPU
browser tests use a host-side dialog executor and packaged inference Worker;
the separate two-Worker test proves packaged dialog execution. Do not collapse
those topologies into an unperformed two-GPU-publisher measurement.

MAP-24's implementation and bounded reference acceptance are complete. MAP-30
still owns extended measured hardware/soak reporting; MAP-31 owns actual public
trust, selected TURN payload and two-hour public acceptance. MAP-29/32 retain
the combined regression, operational rollout and unproven historical startup
incidents. None of those tasks is closed by this profile.

SOLID review: source-clock validation, local observation, signed transport,
Hub policy/admission and test measurement are separate (SRP/DIP/ISP). No Worker
gains task orchestration. The older broad Hub service/authority and native
integration fixture remain acknowledged SRP debt; current features compose
small ports into them without a second orchestration loop or wider interface.
