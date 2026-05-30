# Public Rendezvous Abuse Handling

Public rendezvous endpoints are rate-limited and monitored to reduce abuse.

## Controls

1. **Rate limits**
   - session create, join, signaling send/poll, chat send/poll, view push/poll
2. **Bounded queues**
   - signaling and view queues keep bounded history (backpressure)
3. **Short-lived sessions**
   - default rendezvous session TTL is limited
4. **No plaintext leakage in audit**
   - abuse/audit metadata excludes chat/view plaintext
5. **Revocation**
   - owner can revoke participants and sessions

## Operational actions

- Raise/lower limits by namespace (`rendezvous_*`, `webrtc_*`, `share_*`).
- Revoke abusive participants or sessions immediately.
- Rotate invite codes by creating a new session and revoking old one.
- Temporarily disable public profile for maintenance/incident response.

## Monitoring hints

- Track spikes in `429 rate_limited` responses per namespace.
- Alert on repeated join failures from same IP/user/device.
- Track abnormal signaling volume per session.
- Track queue saturation indicators (frequent truncation to queue max).
