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
