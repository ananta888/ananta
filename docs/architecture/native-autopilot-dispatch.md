# Native Autopilot dispatch

The Hub owns the workflow graph, Task queue, scheduling, policy and result
admission. Native Tasks branch after existing Autopilot gates, assignment and
execution-scope allocation, before model selection or proposal generation.
Native commands do not enter context-driven replanning. Generic Tasks retain
the existing dispatch phases.

`autopilot_native_dispatch` composes six narrow ports: command validation,
assignment binding, signing keys, HTTP transport, result admission and durable dispatch journal. The
bundle is replaceable per Flask application, including background dispatch
with an explicit application reference. This protects DIP and keeps orchestration
separate from persistence and transport. Existing global engine/key composition
and broad Autopilot tick modules remain architectural debt.

## Worker execution port

`POST /api/internal/native-tasks/execute` exists on Worker-role applications.
The route requires a configured bearer token of at least 32 characters and a
JSON body within the 262144-byte bound. Only the Hub signs the
`ananta.native-task-execution.v1` envelope using Ed25519. The envelope binds a
closed Task projection, exact registered Worker identity and URL, and expiry.
The Hub issues a lifetime of at most 30 seconds, bounded by command authority;
the Worker rejects expired envelopes and expiries more than 60 seconds ahead.

The projection contains Task id, tenant, kind, derivation reason and Native
execution context, with optional project/input files. History and stored agent
credentials are excluded. The Worker requires the public Ed25519 verification
keyring, canonical Worker name/URL and existing Native Hub connection settings.
Those recipient values must match the registered Hub assignment. A Worker
cannot mint or broaden command authority. Signed projection verification is
followed by existing live Hub grant, assignment, dispatch-lease and Native
command validation. The execution port imports no Hub services or queue owner.

## Result and failure handling

The existing forwarding/admission pipeline validates the exact Native result
before persisting a Hub receipt or terminal Task. Invalid results and unknown
transport outcomes produce a bounded `native_autopilot_dispatch_blocked`
outcome and attempt the existing review state. A concurrent admitted terminal
result is preserved. Corrupt SQL rows may reject the review update through the
storage guard; dispatch never bypasses that guard. Diagnostics include the
phase and Task id, without private exception payloads.

There is no generic LLM fallback, local Hub execution fallback or blind retry
for an unknown Native execution outcome. Durable outbox reconciliation and
real container-loss recovery remain a separate required operational extension.
No SRC/RUN identity is issued by this transport layer.

## Acceptance scope

`python3 scripts/test_bpmn_container.py --full-app-autopilot` boots the actual
Hub application and a separate Worker, drives authenticated Autopilot HTTP
and checks a Native node and artifact producer/consumer. It uses synthetic
Hub graph admission and deterministic Worker handlers, retaining production
SQL queue, signature, assignment, artifact ingress and receipt services.
It does not establish public production release admission, provider/tool
execution, PostgreSQL concurrency or production image packaging.


## Durable send boundary

`native_dispatch_journal` is an additive Hub SQL table registered with normal
application metadata and the memory-channel inventory. Before HTTP dispatch,
SQL commits one tenant/Task send slot with an internal CAS claim, exact command
and closed-projection digests, Worker identity/URL and assignment revision.
No tokens, signatures or executable payloads are stored in this table.
The unique key prevents concurrent or restarted dispatchers from sending the
same Task again, including after a status reset or a changed assignment.

States are `sending`, `unknown` and `accepted`. A crash after reservation but
before sending intentionally remains uncertain; this initial boundary favors
bounded blocking and does not claim exactly-once external execution. Settlement
and recovery load the actual persisted Hub Task receipt within SQL, validate
its command/result digests and exact Worker assignment binding, then close the
journal. Caller-supplied receipts cannot serve as recovery authority. A later
transport failure cannot regress an accepted entry.

`reconcile_native_dispatches` is an explicit Hub-owned, tenant-scoped batch of
at most 100 pending Tasks. It returns machine-readable accepted/unresolved
results, never creates Tasks, changes their status or resends work. Automatic
scheduler wiring, durable Worker-result redelivery, pre-send recovery and
policy-authorized new attempts remain further operational work.

Independent SQLite engines test reopening, concurrent reservation, forged
receipts and foreign assignments. The full application container gate asserts
accepted journal rows for both Native execution and artifact handoff.
Actual mid-execution container termination is still a separate acceptance gate.
See [the journal gate](../../artifacts/test-gates/bpmn-native-dispatch-journal.v1.json).

## Real Worker loss containment gate

`--full-app-worker-loss` runs the full Hub with a separate Worker that actually
exits after Native execution, before HTTP result transmission. The host checks
the Docker container exit state; the Hub remains alive for follow-up ticks.
The send journal remains unknown, a normal Hub status reset cannot claim another
send, and reconciliation returns unresolved without inventing a Task receipt.
The test runner owns Docker lifecycle on the host; no Worker orchestrates other
Workers or has access to the Docker socket.

A successful containment gate does not establish recovery completion. Durable
Worker-result redelivery, exact current-lease admission after restart,
policy-authorized new attempts and bounded scheduler reconciliation remain
required before claiming operational delivery recovery.

## Durable Worker result retrieval and constrained Hub admission

The productive Worker route composes `SQLiteNativeResultStore` through
`ANANTA_NATIVE_RESULT_STORE_PATH`. Operators must provide an absolute file in a
Worker-owned, private directory (0700; database 0600) on a persistent Worker
volume. Without this configuration the productive execution/retrieval port
returns a bounded unavailable result before execution. No Hub database or
credentials belong in this store. The low-level blueprint retains optional
store injection for existing adapters; productive registration supplies it.

A unique tenant/Task reservation commits before consumer execution. Results
commit before the HTTP response; duplicate execution is refused, including
when the original outcome remains unknown. The store retains the exact closed
Task projection digest and checks it on retrieval. Payloads are bounded to
256 KiB; default retention is 24 hours, maximum seven days. Expired reads scrub
the SQL payload and retain a no-reexecution tombstone. Expiry is logical access
revocation, not a claim of immediate physical erasure. The bounded record cap
fails closed; lifecycle rotation/cleanup policy remains operational work.

`POST /api/internal/native-tasks/result` requires the same short-lived Ed25519,
recipient-bound projection and Worker bearer authorization as execution. It
only reads a stored result and never calls the consumer. Result storage is
untrusted execution output; it issues no SRC/RUN evidence identities.

The Hub's `recover_native_dispatch_results` retrieves at most ten pending
results in an explicit bounded batch. Its transport allows only the result
endpoint, disables automatic retries and redirects, limits response bytes and
closes streamed responses. Recovery checks the original journal/assignment
before retrieval. Admission uses the existing Native SQL policy and current
lease, registered Worker and immutable grant. Only transport-review Tasks with
`native_autopilot_dispatch_blocked` and an exact pending journal binding may
be completed from review; unrelated/manual review remains denied. Pi retains
its separate stricter admission policy.

Journal settlement locks Task before journal, matching recovery admission and
avoiding a newly introduced PostgreSQL lock-order cycle. Existing global SQL
and signing-key composition remain preserved DIP debt; production factories
compose those dependencies into the injected recovery ports.

Independent Worker store reopening, signed HTTP retrieval and actual Hub SQL
admission are verified with synthetic fixtures. Full-App containers verify
ordinary execution/publication and loss containment. Worker recreation with a
persistent volume, complete result redelivery in that container chain,
scheduler wiring, retention maintenance, PostgreSQL and production packaging
are still separate acceptance obligations. Tool/container filesystem isolation
of the private store is not established by POSIX ownership checks alone.


## Verified full application recreation/redelivery follow-up

`--full-app-worker-redelivery` now verifies the previously open container-chain
boundary. The Worker publishes to its own private persistent Docker volume,
exits before response, and is recreated by the host fixture after exact exit
inspection. Only the host performs this infrastructure action. The Hub uses
its productive recovery ports, signed retrieval transport and forwarding/SQL
admission. It completes the original Task, journal claim and workflow from the
old Worker's persisted result; no consumer reexecution occurs.

The accepted result's original Worker hostname differs from the new container,
and cleanup verifies that containers, networks and volumes are gone. The test
volume initializer is networkless, mounts only this fresh Worker volume and
has CHOWN as its sole additional capability; execution Workers remain uid 65534.
This is synthetic technical evidence, not production release admission.
Scheduler integration, expiry/new-attempt recovery, retention/tool isolation,
PostgreSQL and packaging remain open. Host lifecycle/output parsing are
separated into focused helpers (SRP); existing global application composition
remains preserved debt.


## Opt-in reconciliation on the existing Hub scheduler

Hub configuration `ANANTA_NATIVE_RESULT_RECOVERY_ENABLED=true` or environment
`ANANTA_NATIVE_RESULT_RECOVERY_ENABLED=1` enables automatic result retrieval.
Default is disabled; explicit application false overrides the environment.
Only Hub-role applications participate. The existing tick's guardrails,
terminal-goal stop and scoped Task listing run before this extension. Stop
signals and unrelated/manual review reasons never construct recovery ports.

Each tick selects one transport-review Native Task from its scoped list. The
existing persisted tick count rotates candidates, so a blocked first Task
does not permanently starve later ones. SQL pending lookup restricts both
tenant and exact Task, and recovery rechecks Goal/Team against the tick snapshot
before fetching. No new process state, scheduler loop or worker delegation is
introduced. Current lease/grant/assignment checks remain mandatory at admission.

A successful reconciliation reports `recovered=1`, `dispatched=0` and terminal
result counts, persists existing loop counters, and returns before stale-task
or generic dispatch processing can operate on the old snapshot. Unresolved
outcomes continue through existing tick handling. The extension owns policy/
selection only; transport, SQL and result admission remain separate ports (SRP/DIP).
The broad legacy tick engine and global runtime composition remain existing debt.

The complete Worker recreation gate now invokes authenticated Autopilot HTTP
instead of directly calling the recovery service. It passes with Task/journal/
workflow completion, preserved original result and zero recovery dispatches.
Lease-expiry/new-attempt recovery, retention/tool isolation, PostgreSQL and
production packaging/release admission remain separate work.

## Expired dispatch and explicitly authorized new attempts

Expired result admission remains denied. A signed workflow retry with any
`sending` or `unknown` Hub dispatch in the run now requires an exact Hub-owned
`ANANTA_NATIVE_UNCERTAIN_RETRY_POLICY` binding. Actor roles, command metadata
and a targeted step cannot bypass this run-wide check. Every uncertain dispatch
must be covered, with at most 100 dispatches and 100 policy entries examined.
Missing application/Hub context or SQL failure denies the retry.

Each entry has exactly these fields: `purpose` (value
`native.retry.unknown_dispatch`), `tenant_id`, `workflow_id`, `run_id`,
`plan_hash`, `policy_version`, `step_id`, `actor_id`,
`dispatch_binding_digest` and `maximum_attempts`. All identities must match
the Hub's persisted original dispatch and current signed command/plan.
The attempt maximum is an integer from two to ten, bounded by the compiled
plan budget. Exhausted budgets and additional/wildcard fields deny permission.
Known outcomes without uncertain journal rows preserve existing retry behavior.

The bounded SQL policy reader has a separate responsibility from command
application, transport and scheduling (SRP/ISP). Existing global engine and
Flask configuration composition remain DIP debt. Cancellation obtains its
allowed active statuses from the existing state machine, while the same
transaction still validates the signed run-control lease and Task binding.
This covers transport review and dependency blocking without weakening fencing.

`--full-app-expired-retry` recreates the real Worker after durable publication
and response loss, then expires the SQL execution lease as an explicit fault
fixture. The authenticated scheduler rejects the old result; a signed cancel
succeeds; retry without policy fails. Exact Hub preauthorization then completes
a new Task with a new attempt and a higher fence. The old journal remains
unknown and receives no result receipt. Controls use the production orchestrator
programmatically; dispatch and result recovery use authenticated Autopilot HTTP.
The graph and handler are synthetic, and SQL expiry is injected rather than
observed through wall-clock waiting. This does not establish external-provider
exactly-once effects or production release evidence. Retention/tool isolation,
PostgreSQL and production packaging/release admission remain open.

## Bounded result retention maintenance

The private SQLite store now scrubs one batch of expired `ready` or `executing`
slots when opened and before reserve, publish or read. Maintenance updates
only expired rows selected through a partial expiry index; it does not hydrate
result content. The default batch is 100, with an explicit maximum of 1000.
Live results remain untouched. Payload and digest become NULL; tenant/Task and
projection binding remain as replay tombstones. Expired executions cannot
publish or execute again. Tombstones still count toward the store quota: no
capacity recovery silently permits old Task replay.

An idle Worker owner can execute one finite maintenance invocation:

```sh
python -m worker.runtime.native_graph.result_maintenance --batch-size 100
```

It requires the explicitly configured `ANANTA_NATIVE_RESULT_STORE_PATH`, emits
only bounded JSON counts/status, and reports failure without paths or content.
An invalid batch cannot create a database. It starts no scheduler, modifies no
Hub Task and issues no evidence identity. An operational cadence must use the
Hub's existing delegated maintenance or deployment administration; an idle
store does not promise wall-clock deletion without invocation.

Every SQLite connection enables `secure_delete`; payload scrubbing is not a
cryptographic disk/snapshot erasure guarantee. The maintenance method remains
part of the focused storage adapter and the CLI only composes/calls it (SRP).
The broader Native namespace still imports shared contracts from the Hub
package; its standalone packaging debt is preserved and remains separate work.
0700/0600 alone protect against other UIDs and cannot isolate tools sharing the
Worker's UID. The Shell-port isolation below closes that boundary for its
configured store; other execution ports still require equivalent integration.

## Private result-store isolation at the Shell execution port

When a private result store is configured, `execute_command_plan` now launches
tools in a separate isolated-mode Python launcher. Configuration comes from the
active cached store, current application or deployment, never tool environment
overrides. Clearing configuration cannot disable the active store's isolation. Only the
workspace and fixed read-only runtime resources receive filesystem grants.
Overlapping store/workspace/resource hierarchies are denied. A pinned O_PATH
workspace descriptor survives pathname replacement and closes before exec;
unrelated descriptors are closed and stdin is DEVNULL.

Landlock ABI >=6 and its disconnected-directory erratum 3 fix are mandatory.
Read/write/create/remove/refer/truncate/ioctl rights are handled; signals and
abstract Unix IPC are scoped. Restrictions also cover descendants. A required
Seccomp filter denies metadata mutations, ioctl (except descriptor-only
FIOCLEX/FIONCLEX/FIONBIO), mount/descriptor/process-memory
escape and io_uring. Foreign/compat architectures and syscalls >=452 are denied.
This also denies chmod/chown/xattr/timestamp operations inside the workspace;
ordinary content writes and creation modes still work. Root execution is denied.
Only PATH/locales survive, with HOME/TMPDIR bound to the workspace. Setup failure
returns exit 126 and `native_tool_filesystem_isolation_denied`, without fallback.

Policy composition, kernel ABI adapter, metadata filter and launcher have focused
responsibilities (SRP/ISP). The existing command port's mixed approval/invocation/
result concerns remain SRP debt, and Flask/environment composition coupling is
preserved. No preexec_fn, parent-thread security mutation or Worker orchestration
is introduced. Hub policy/queue/lease ownership is unchanged. Kernel references:
[Landlock](https://docs.kernel.org/userspace-api/landlock.html),
[Seccomp](https://docs.kernel.org/userspace-api/seccomp_filter.html),
[syscall table](https://raw.githubusercontent.com/torvalds/linux/master/arch/x86/entry/syscalls/syscall_64.tbl).

`--full-app-tool-isolation` dispatches through real authenticated Autopilot HTTP
into a separate Worker. Static adversarial commands run the production Shell
port against its actual private SQLite store: same-UID read/write/truncate/chmod
are denied while workspace writes succeed. Native and producer/consumer
workflows complete with actual Hub receipts. Inputs, handlers and adversarial
commands are synthetic, not production release evidence. Positive kernel
execution is verified on x86_64; aarch64 needs a separate execution gate.
Other provider/tool launch ports, network egress, pathname metadata visibility,
resource/output buffering and complete deployment sandbox profiles remain open.
Idle maintenance, authorized tombstone retirement, PostgreSQL and production
release admission also remain separate work.
Idle maintenance must execute in an explicitly authorized owner/administration
context; the ordinary sandboxed Shell port intentionally cannot write this store.

The Native Pi default provider now injects a narrow process-launch port into
`BoundedCodingAgentProcess`. Its SDK execution uses the same mandatory kernel
guards when a private result store is configured. The ephemeral `work` directory
is writable; its bound `config`, fixed SDK entry/transport, verified package
dependency tree and runtime executable are read-only. Only the selected
`ANANTA_PI_API_KEY` and explicit Pi runtime variables survive; Hub/service tokens
and loader overrides are excluded. Version probes use a disposable workspace
and receive no provider credentials. No private-store configuration retains the
existing direct-launch compatibility path.

Policy stays in Worker composition; the shared bounded-process implementation
depends on a small launch protocol (DIP/ISP) and retains timeout, cancellation,
output limits and process-group cleanup. Existing Worker dependencies on the
Hub-packaged CLI subsystem remain packaging/coupling debt. Local tests execute
synthetic SDK/probe scripts under real x86_64 kernel restrictions; they do not
prove a live pinned Pi SDK/provider run or production release admission.
The real Node 24 runtime is also checked with synthetic SDK scripts and the
catalog's `dist/bundle/cli.js` layout. Node requires read-only system OpenSSL
configuration and FIONBIO for its piped stdio. The CLI probe explicitly grants
the selected Node interpreter alongside its package tree; it fails closed if
that interpreter is missing. Tests deny inode/device ioctls while allowing
close-on-exec and nonblocking descriptor flags. No broad ioctl or writable
runtime/configuration grant is introduced.

The opt-in `tests/pi/test_pi_pinned_sdk_isolation.py` gate now executes the actual
pinned SDK 0.85.1 and production SDK entry against a bounded synthetic loopback
OpenAI streaming endpoint. It verifies one selected-model request, token bound,
no tools, selected authentication and no model-cache file. The SDK's default
file credential store tries to create an auth lock even for reads; the entry
therefore supplies an immutable, one-provider credential port in memory.
Foreign-provider reads and credential mutation/deletion fail closed. This
protects SRP/ISP and avoids granting writes to trusted configuration. No SDK
internals or private storage API are imported. The test is a technical
observation with synthetic model output, not live-provider or release evidence.
Use explicitly prepared `ANANTA_TEST_NODE_BINARY` and `ANANTA_TEST_PI_SDK`
resources; ordinary test execution never downloads packages. The local gate
used an isolated non-root installation with `--ignore-scripts`, no capabilities
and a read-only container root, removed after installation.
