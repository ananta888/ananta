# Receiver quality observation contract

## Purpose and authority boundary

The v1 receiver quality observation is a short-lived hint from one admitted
browser instance about one received publication. The hub may use an accepted
observation as an input to a receiver-specific layer policy. The hub remains
the sole control plane and owns membership, subscription, routing, layer
allowance, policy, queueing, and every resulting command.

The observation is not an authorization document. authorization_effect is
always none and advisory_only is always true. requested_layer is a receiver
preference, allowed_layer is only an echo of the current hub-owned value, and
effective_layer is a measurement of what the receiver rendered. None of the
three creates, widens, proves, or renews a subscription or layer entitlement.
The hub rejects an allowed_layer echo that does not exactly match its atomic
scope snapshot. Workers may consume delegated work but do not accept quality
reports, mutate policy, or orchestrate other workers.

The schema is:

- Local path: schemas/webrtc/receiver_quality_observation.v1.json
- Canonical id: https://ananta.local/schemas/webrtc/receiver_quality_observation.v1.json
- Schema version: 1
- Observation vocabulary: bounded-v1

No SRC_* or RUN_* identifiers were supplied for browser, WebRTC, or vendor
behavior. This contract therefore makes no source-grounded claim that a
browser exposes or measures any listed metric. An unavailable metric is
omitted. It is never guessed, synthesized, or replaced by an invented source
identifier.

## Fixed metric vocabulary and units

Every metric is an integer. Field names carry the only permitted unit so that
senders cannot select a different scale. At least one metric is required in a
sample. Unknown properties, floating-point values, numeric strings, NaN, and
positive or negative Infinity are not accepted.

| Field | Unit | Inclusive v1 range | Meaning |
| --- | --- | ---: | --- |
| rtt_ms | milliseconds | 0..60000 | receiver-observed round-trip time |
| jitter_ms | milliseconds | 0..10000 | receiver-observed inter-arrival jitter |
| packet_loss_basis_points | basis points | 0..10000 | lost packets as 1/100 of one percent |
| receive_bitrate_bps | bits per second | 0..1000000000 | received media bit rate |
| decode_time_ms_per_frame | milliseconds per decoded frame | 0..1000 | decode time |
| freeze_duration_ms | milliseconds in sample window | 0..2000 | frozen video duration |
| audio_gap_duration_ms | milliseconds in sample window | 0..2000 | missing audio duration |
| buffer_level_ms | milliseconds | 0..60000 | receiver media buffer level |
| viewport_width_css_px | CSS pixels, multiple of 16 | 16..8192 | coarse render width |
| viewport_height_css_px | CSS pixels, multiple of 16 | 16..8192 | coarse render height |
| cpu_pressure_basis_points | basis points | 0..10000 | fraction of the sample window under browser-reported CPU pressure |

Viewport width and height must occur together. Exact device pixels, device
pixel ratio, screen size, display identity, and monitor inventory are
prohibited. Freeze and audio-gap duration must not exceed their sample's
window_ms even though each field also has an independent schema maximum.

The boundary accepts only standards-compliant JSON. Raw NaN, Infinity, and
-Infinity tokens fail before parsing with invalid_json_non_finite. A
permissive decoder returning a non-finite host value must produce the same
failure. Quoted numeric sentinels and other numeric strings fail closed as
schema_invalid. Negative values use numeric_negative. Values above a fixed
range use metric_out_of_range. Relationally impossible durations use
metric_exceeds_sample_window.

## Separate layer observations

requested_layer, allowed_layer, and effective_layer are always separate root
fields. Each is either null or a closed spatial_id and temporal_id pair in
the range 0..3.

allowed_layer has one additional required value: source is
hub_state_echo. On receipt the hub compares the complete echo with the
currently active layer allowance for the exact tenant, room, subscriber,
publication, browser pseudonym, and route epoch. null must likewise match a
hub-owned null allowance. A mismatch is rejected rather than interpreted as
a request.

The hub computes any new allowance from authoritative policy and may choose
to ignore requested_layer and effective_layer. It never derives an
entitlement from an observation. No subscription_ref, role, permission,
audience membership, consent, key epoch, routing target, topology choice, or
policy override is legal in the payload.

## Hard limits

All limit values are required constants. They describe receiver and hub
enforcement; they are not sender-provided budgets.

| Field | v1 value | Enforcement |
| --- | ---: | --- |
| history_reports_max | 12 | at most twelve accepted reports retained for this exact scope |
| samples_per_report_max | 16 | samples array has one through sixteen entries |
| reports_per_minute_max | 12 | at most twelve accepted reports in the preceding rolling 60 seconds |
| report_bytes_max | 8192 | maximum UTF-8 request body before parsing |
| sample_window_ms_max | 2000 | each sample window is 100..2000 ms |
| history_window_ms_max | 30000 | no sample may precede issued_at by more than 30 seconds |
| observation_age_ms_max | 5000 | report must reach the hub within 5 seconds of issued_at |

The receiver may report less often and may omit unavailable metrics. It may
not raise a constant. The hub measures bytes before parsing and rate before
committing the report. A report that would be the thirteenth accepted report
inside the rolling window is rejected. Rejected reports do not advance rate,
sequence, or history state.

History is volatile and scoped by the full binding tuple. After an accepted
report the hub drops entries older than 30 seconds and then drops the oldest
entries until at most twelve remain. The hub never persists this history in
analytics, logs, profiles, worker storage, or another subscriber,
publication, browser, route, room, or tenant scope.

## Scope, time, and monotonicity

Each report is bound to all of:

- tenant_ref
- room_ref
- subscriber_ref
- publication_ref
- browser_instance_pseudonym
- route_epoch
- sequence

The hub validates those values against one atomic, hub-owned snapshot. A
browser pseudonym is opaque, room-scoped, admission-scoped, and ephemeral.
It is not a user, account, installation, device, analytics, or persistent
client identifier.

sequence must be strictly greater than the last accepted report sequence for
the complete binding tuple. Within a report, sample_sequence and observed_at
must each be strictly increasing. Samples must not be later than issued_at,
and issued_at must not be later than hub time. A lower route epoch is stale;
a higher epoch was not issued for that scope. Epoch changes establish a new
scope but do not authorize copying old observations into it.

## Privacy boundary

Closed JSON objects reject every unknown field. Before ordinary schema
reporting, the boundary performs a recursive denylist scan of property names
and values so prohibited data receives a stable privacy reason. Aliases,
nested values, and differently cased names are covered.

The following classes are prohibited:

- IP addresses, host addresses, ICE candidates, TURN addresses, and network-interface details
- device IDs, device labels, group IDs, hardware names, screen identity, and device inventory
- SDP, codec fmtp blobs, DTLS fingerprints, raw RTP or RTCP records, and signaling dumps
- encoded or decoded audio, video, image, frame, waveform, media payload, and recording data
- transcript, caption, subtitle, speech text, prompt, message, and semantic content
- embedding, feature vector, tensor, descriptor, biometric template, and model input or output
- user-agent, platform, locale list, CPU model, GPU model, memory size, and hardware concurrency
- account, installation, analytics, persistent browser, persistent session, or stable client identifiers
- subscription, role, permission, audience, consent, key, routing, topology, or policy claims

Only the bounded aggregate metrics in the schema are admitted. Accepted
payloads and history remain volatile and are purged on browser pseudonym
rotation, publication end, subscriber leave, route change, admission
replacement, room close, or expiry, whichever occurs first.

## Validation order and stable reason codes

The hub boundary applies this order:

1. Reject a body over 8192 UTF-8 bytes.
2. Reject invalid JSON and non-finite raw numeric tokens.
3. Recursively reject privacy data and authority claims.
4. Validate the complete closed schema and fixed constants.
5. Match tenant, room, subscriber, publication, and browser pseudonym.
6. Match route epoch and the echoed allowed layer against one atomic hub snapshot.
7. Validate hub time, report age, sample windows, sample order, and physical relations.
8. Enforce report sequence and rolling report rate.
9. Atomically commit sequence, rate entry, and bounded volatile history.

| Code | Meaning |
| --- | --- |
| ok | observation accepted |
| report_bytes_exceeded | received UTF-8 body exceeds 8192 bytes |
| invalid_json | body is not standards-compliant JSON |
| invalid_json_non_finite | raw or decoded NaN or Infinity value is present |
| privacy_ip_forbidden | address, ICE, TURN, host, or interface data is present |
| privacy_device_forbidden | device label, identifier, hardware, display, or inventory data is present |
| privacy_sdp_forbidden | SDP, DTLS, fmtp, raw RTP/RTCP, or signaling data is present |
| privacy_media_forbidden | audio, video, image, frame, waveform, payload, or recording content is present |
| privacy_transcript_forbidden | transcript, caption, subtitle, speech, prompt, message, or semantic content is present |
| privacy_embedding_forbidden | embedding, vector, tensor, descriptor, biometric, or model data is present |
| persistent_identity_forbidden | stable account, installation, analytics, browser, session, or client identity is present |
| authority_claim_forbidden | subscription, role, permission, audience, consent, key, routing, topology, or policy claim is present |
| unknown_field | a non-denylisted property is outside the closed schema |
| limit_contract_mismatch | a required v1 limit constant was changed |
| sample_count_exceeded | samples array contains more than sixteen entries |
| numeric_negative | a metric or duration is negative |
| metric_out_of_range | a metric exceeds its fixed physical bound |
| metric_exceeds_sample_window | freeze or audio-gap duration exceeds window_ms |
| schema_invalid | any other schema failure, including numeric strings or fractions |
| cross_tenant_observation | tenant differs from active scope |
| cross_room_observation | room differs from active scope |
| cross_subscriber_observation | subscriber differs from active scope |
| cross_publication_observation | publication differs from active scope |
| browser_pseudonym_scope_mismatch | browser pseudonym differs from active scope |
| stale_route_epoch | route epoch is below the active epoch |
| route_epoch_unissued | route epoch is above the active epoch |
| allowed_layer_echo_mismatch | allowed_layer differs from current hub state |
| observation_from_future | issued_at is later than hub time |
| stale_observation | report age exceeds five seconds |
| sample_from_future | observed_at is later than issued_at |
| stale_sample | sample precedes the 30-second history window |
| sample_window_exceeded | window_ms exceeds 2000 |
| sample_order_invalid | observed_at values are not strictly increasing |
| sample_sequence_invalid | sample_sequence values are not strictly increasing |
| observation_replay | report sequence is not strictly greater than the last accepted sequence |
| report_rate_exceeded | accepting the report would exceed twelve reports in the rolling minute |

## Deterministic fixtures

Fixtures live in
tests/fixtures/webrtc/receiver_quality_observation/. Valid fixtures contain
validation_context, instance, and expected. Negative fixtures name a valid
base, mutate instance or validation_context by JSON Pointer, and state the
stable phase and reason code.

The NaN and Infinity fixtures use replace_with_non_json_numeric_token with a
raw token because those values cannot occur in a valid JSON fixture document.
The harness substitutes the token into the serialized base document and
exercises the pre-parse boundary.

The fixture set covers full and maximum-size valid reports; every prohibited
privacy class required by this contract; authority expansion; NaN, Infinity,
negative and implausible values; byte and sample oversize; fixed limits,
window and rate bounds; stale reports, samples, route epochs and sequences;
and cross-tenant, room, subscriber, publication, and browser scope.
