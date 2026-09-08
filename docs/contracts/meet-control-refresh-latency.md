# Diagnose bounded control freshness during GPU speech

Source audit: Ananta `89956ecaf`, Meet `1c7cd3c`; packaged Worker
`sha256:01db5060da48dd832588c84e00e2454b6792020a8236657ed1936f7df33de984`.
The client probe itself passes 330 root regressions, the complete isolated Meet
check and both real two-packaged-Worker screen/persona/speech cases (105.70 s).

The subsequent actual selected-voice GPU dialog failed in 110.73 s. Both Qwen/
Piper answers were generated: 46,592 neutral-voice samples, then 109,824 whisper
samples. During the second output the runtime raised
`meet_dialog_control_state_stale`; the pending output was cleared. Failure
diagnostics show a 25.06-ms polling gap, 18.08 ms beyond the established freshness
deadline, and a still-pending 1,609.21-ms control request. A prior browser call
classified only as `other` took 1,320.71 ms. Completed Hub-domain exchanges were
48–64 ms. These measurements do not establish whether delay occurred before
the domain handler, in HTTP transport, executor scheduling or browser execution.

Do not extend the 2.5-second freshness/stop boundary or relabel the interrupted
audio as delivered. A successful repeat alone is not a timing fix. First extend
only private test observation: distinguish bounded browser operation categories,
correlate numeric monotonic timings, measure the existing complete Worker-Hub
exchange independently of its domain handler, and retain a bounded control-state
transition history. No additional browser/Hub request, result consumption,
retry, authority write, keys, payloads, URLs or exception text in diagnostics.

Verify observers with virtual clocks and the actual wrapped methods, including
native return/exception identity and bounded copied output; then repeat the
same actual GPU scenario to localize the delay. Any scheduling change must have
a deterministic failing regression and preserve fresh Hub authorization,
single-flight/backpressure, original assignment expiry and terminal denial.
MAP-08/11/24 remain open. This is synthetic-policy technical runtime testing,
not production release evidence or a completed cold-start/soak/public gate.

SRP/DIP: transport, browser and control-state observation remain separate small
test adapters. Existing broad runtime/test composition debt is preserved; no
Worker-owned task scheduler or timing policy belongs in an observer.
