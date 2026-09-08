# Independent control-progress watchdog for a stalled dialog runtime

## Source audit and remaining stop gap

At `89fe9db32`, the dialog runtime checks its 2.5-second control freshness
while its browser loop can run. Its parent `DialogExecutor` only enforces the
whole assignment deadline plus five seconds. A browser RPC that never returns
can therefore prevent the runtime from reaching its next local freshness
check. The earlier source-page crash fix bounds sanitized snapshot reads; it
does not make every legacy browser call interruptible.

Add a separate resource-progress watchdog at the existing Worker parent, not
a new Hub scheduler or a replacement authorization path. A private inherited
Unix datagram descriptor carries only the absolute monotonic freshness deadline
of a newly validated current Hub state. The runtime closes descriptor inheritance
before launching browser children. No assignment, grant, transcript, identity or
media crosses this channel. A delayed or repeated heartbeat cannot renew time.

The parent captures the original assignment hard deadline once. Before the
first validated state, startup is capped at 90 seconds and the original bound;
no publication permission follows from this startup grace. Once active, the
resource deadline is the unchanged 2.5-second control expiry plus at most
500 ms for cleanup, always capped by the original bound. Malformed, regressing,
expired, excessively future or flooded progress fails closed. Neither a timer
nor the watchdog may obtain new grants, redispatch, restart, change sources or
extend any Hub/Meet authorization. The existing source-level freshness fences
remain stricter and unchanged. The parent terminates only its assigned child
process group and releases the existing slot once.

Separate pure budget validation, private descriptor transport and process
watching (SRP/DIP). Keep the existing executor and runtime as composition roots;
do not add policy to source pumps. Compatibility: direct test-owned runtime
invocation without the private descriptor retains the existing local checks;
the installed executor always supplies the additional watchdog.

Verification must include bounded real child processes, delayed/duplicate
messages, immutable replay fences, descriptor inheritance and failure cleanup.
Then freeze only a freshly created test runtime/browser while its actual
receiver is observing motion. Require bounded departure, no surviving owned
browser descendants and no manufactured terminal report. Do not claim a
network/receiver hard-real-time SLA from a Python timer, or close MAP-11 before
its separate reconnect/restart criteria are satisfied.

## Implemented parent/runtime boundary

`DialogProgressBudget` owns pure monotonic deadline validation and irreversible
failure. `DialogProgressChannel` owns the nonblocking eight-byte datagram and
descriptor lifecycle; `dialog_progress_watch` polls independently in the
already-existing executor watch thread. The runtime reports only after its
current local membership and returned Hub state agree, before applying source
updates or renewal. A failed progress write stops the runtime. Inheritance is
disabled and the descriptor environment variable consumed before browser spawn.

The parent retains one original hard bound, a 90-second startup maximum, and
the unchanged control expiry plus 500-ms resource-cleanup allowance once active.
The channel has a 16-packet per-tick maximum. Terminal/expired/malformed progress
cannot reopen the budget. Every watch path closes descriptors and releases the
existing slot in `finally`; SQL replay reservations remain durable. No approval,
new task, grant, media replay or authority fallback was introduced.

Initial verification: **85 budget/channel/executor/replay/runtime regressions
passed in 38.82 s**, then **12 actual-process and runtime-composition checks
passed in 16.67 s**. The latter exercise real stalled/silent/malformed children,
normal exit and a real descendant with the progress descriptor closed even
when `close_fds=False`. The unchanged process-group stop reaps the exact owned
child, and invalid membership never emits progress. Ruff and the standalone
Worker boundary check (**104 files**) passed. Installed-browser descendant and
receiver verification is still required; these process tests alone do not
prove Chromium's separately spawned subprocesses disappear.

## Installed browser and receiver verification

Immutable image
`sha256:03e5576cc841570aae8fe80ce68975bfad2d76da2bccae40266bfc7e8ec064d2`
was built from `bf065bdef`, without application-source mounts. Against the
separately built Meet `92f583f`, the first selection passed read recovery and
terminal-error fencing. Its stall case failed before injection during browser
launch (**2 passed/1 failed, 124.70 s**); the old test diagnostic redacted the
underlying launch reason. A bounded enum-only launch diagnostic now distinguishes
timeout, sandbox, resource, executable, crash and unknown, without retry,
Chromium logs or launch arguments. The cause of that first startup failure
remains unestablished; a passing repeat is not a fix for it.

The stall repeat passed in **159.69 s**. After both remote screens were moving,
the probe resolved exactly one owned runtime by process identity and start
time, then applied SIGSTOP only to that runtime. The parent watchdog removed
it and all **nine observed browser descendants in 2139.86 ms**. No active
untracked Chromium/driver process remained. The Worker container stayed running
and its native health probe succeeded. The departed participant disappeared;
the other screen continued and subsequently obeyed independent operator
revocation (**216.78 ms**). Native Hub reconciliation later failed the orphan
at its unchanged real-clock deadline with exactly one history event. Its
terminal Worker observation correctly remains missing, not fabricated.

The test uses kernel suspension of the real runtime to represent an event loop
that cannot progress; it does not claim to diagnose an actual hung browser RPC.
The first new probe file also had an invalid NUL literal during test collection;
its byte splitting was corrected and module/probe syntax checked before the
successful run. Ten stall/cleanup helper tests passed in **12.79 s**, twelve
closed launch-diagnostic tests in **14.25 s**, and 35 completion/crash/observation
regressions in **31.18 s**. A narrow test double was updated to accept the new
optional progress port; production cleanup order was not relaxed.

The final **152 selected regressions passed in 63.47 s**. Two additional full
installed-container cases passed in **120.70 s**: simultaneous persona/speech
and separately owned browser workspaces, including their independent source,
membership and operator-stop fences. This also rechecks the extracted terminal
observation helper in actual containers. These remain private, synthetic-policy
technical observations, not GPU, public TURN, full-Hub restart, automatic room
rejoin or production release evidence. MAP-11 remains partial.
