# SFU broadcast egress adapter

The adapter is a narrow infrastructure boundary. The Hub remains the policy
owner; the adapter neither schedules packets nor selects browser receiver
layers.

## Pinned public LiveKit surface

The selected `LiveKitControlApiClient` exposes subscription changes through
the documented `RoomService.UpdateSubscriptions` Twirp API. Subscribe,
unsubscribe, and route replacement are therefore reported as
`accepted_unverified` unless a separate authoritative runtime acknowledgement
is available. Participant listing is non-authoritative for route state.

The pinned public client does not advertise native queue, scheduler,
starvation, receiver-disconnect, or keyframe hooks. These operations return
`sfu_egress_capability_unsupported`; the adapter does not simulate them.

## Optional runtime extension

An optional runtime action requires all of the following:

- an authenticated and fenced `SfuRuntimeControlCommand`
- authorization bound to tenant, room, publication, route epoch, topology
  epoch, fencing token, and signed fairness-profile digest
- an exact capability value of `available` from the runtime capability port
- an authenticated acknowledgement from the runtime-control boundary

Missing or stale evidence disables only that optimization.

## Observations and accounting

Egress observations contain only identifiers, epochs, time-window bounds,
actual or estimated byte counters, observable drop counts, and receiver
counts. Payload samples and arbitrary labels are rejected. Actual,
estimated, and missing accounting values remain distinct. Shared-processing
savings are never counted as network egress.

## Evidence boundary

Unit and fake-adapter tests prove authorization, idempotency, capability
fallback, and payload-blind validation. Real stalled/bursty/normal receiver
isolation, RAM/FD bounds, restart cleanup, and vendor egress counters require
a pinned LiveKit container/runtime-extension evidence run and are not claimed
by this implementation.
