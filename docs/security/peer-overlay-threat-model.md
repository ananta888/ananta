# Peer overlay threat model

## Trust boundaries

- The Hub authorizes membership, epochs, routes, tickets and release stages.
- A participant may decrypt only publications for which the existing key
  authority has granted access.
- A relay is untrusted for confidentiality, delivery and truthful self-reporting.
- LiveKit and TURN are transport infrastructure, not membership authorities.
- Workers remain task executors and never receive peer orchestration authority.

## Threats and controls

| Threat | Prevention / containment | Detection | Recovery | Residual risk |
| --- | --- | --- | --- | --- |
| Forged membership or edge | Hub signatures, closed fields, tenant/room/publication scope | signature and scope rejection | fetch fresh Hub snapshot | Hub signing-key compromise is out of scope for a peer |
| Replay or stale route | separate epochs, expiry, nonce, durable one-use tickets, bounded replay keys | deterministic reason codes and counters | request a fresh Hub plan | a peer can still replay before first accepted consumption on another endpoint unless consumption remains Hub-mediated |
| Loop or route poisoning | Hub-generated acyclic DAG, signed lease, authenticated route metadata, hop/path bounds | loop, duplicate and budget counters | control-only or fresh topology | compromised authorized endpoints can drop their own traffic |
| False relay capacity | effective capacity is the minimum of self and observed capacity | delivery observations | Hub replan | sparse observations conservatively reduce admission |
| Selective drop/delay | two-observer quorum per closed traffic class, sample minimum, cooldown, signed backup lease | class-specific delivery ratio and delay thresholds | one-use backup ticket; no permanent duplicate stream | dropping cannot be prevented cryptographically |
| Complaint abuse | one vote per active member and traffic class; stale epochs ignored | bounded active-member observation set | retain primary until same-class quorum | colluding members can still degrade availability |
| Queue exhaustion | per-child/per-class message and byte caps, TTL, ciphertext cap, replay cap | content-free drop counters | isolate slow child and use fallback | traffic analysis remains possible |
| Key/plaintext exposure at relay | opaque ciphertext-only relay interface; no decrypt/key export API | negative interface and payload tests | revoke membership and rotate key epoch | an authorized recipient can record plaintext; this cannot be prevented |
| IP disclosure | explicit consent and per-edge relay-only ICE policy | selected candidate-pair stats, where available | revoke consent or use TURN/SFU | direct peers learn connection metadata |
| Hub partition | no new publications, route changes or peer lease extensions during bounded grace | grace deadline and stale-epoch rejection | rejoin through Hub | current traffic may stop before connectivity returns |

Diagnostics must not include SDP, ICE credentials, keys, plaintext, full IP
addresses or media/chat content. Stored topology records are tenant-scoped and
contain logical peer identifiers only. Runtime evidence remains unverified until
an authorized evidence registry supplies the exact source and run identifiers.

Relay code owns opaque ciphertext queues only. Removing an edge clears its
bounded queues immediately; no content key, key export or decrypt capability is
part of the relay surface. The production CSP permits same-origin/blob workers,
denies plugins and framing, and the frontend release workflow rejects high or
critical production dependency advisories.
