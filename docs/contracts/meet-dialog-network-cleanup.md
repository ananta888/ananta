# Private dialog network ownership (MAP-29/30)

On 2026-09-09 the paired-idle browser reference could not provision its private
bridge: Docker reported that all predefined address pools were fully subnetted.
Fourteen `meet-test-tls-*` networks remained. This observation does not authorize
pruning unrelated Docker resources or changing daemon address pools.

The cross-repository teardown closed the bridge before its Python-owned isolated
peer browser. The bridge's final network removal therefore encountered a live
endpoint. A bridge failing an observation can also exit before the parent test
has reached teardown. Removing only the containers afterward leaves that network.
This ordering defect explains a leak path, not the provenance of every old net.

The parent already owns the private bridge process and both browser containers.
It now captures the exact immutable Docker network ID from the private setup
handoff, requiring the fixture name, internal isolation and bridge driver. The
focused network-cleanup adapter does not own application policy or browser logic.
Teardown attempts the Worker browser, peer browser, bridge, then final network
cleanup, even if an earlier close fails. A network already removed by the bridge
is success. Otherwise its exact ID, name, driver, isolation and empty endpoint
map must still match; no endpoint is disconnected, and no prefix scan, prune,
guessed subnet or same-name replacement is used. Docker also refuses removal
if an endpoint attaches after inspection.

Removal failure remains a failure and retains the cleanup capability. The same
rule now applies to the browser container's `created` flag. This is test-owned
resource lifecycle work, not a production runtime or trust change. It protects
SRP by separating disposal identity from browser provisioning; the large
cross-repository scenario remains existing SRP debt, with reduced teardown
nesting rather than another responsibility embedded in the scenario.

Unit checks cover immutable identity, changed isolation, occupied endpoints,
same-name replacement, missing/ambiguous lookup, partial setup, every cleanup
failure position and idempotence. Native validation must additionally check that
the exact test network disappears after the actual browser reference.

The 67 focused cleanup/browser/handshake checks passed in 34.61 seconds;
Ruff and Todo consistency checks passed. Native cleanup validation is pending.
