import {
  PeerOverlayControlReplication,
  ValidatedMembershipEvent,
} from './peer-overlay-control-replication';

describe('PeerOverlayControlReplication', () => {
  it('applies only verified linear Hub events and acknowledges over the control lane', async () => {
    const harness = createHarness();
    await harness.replication.accept({ kind: 'membership_event', event: { sequence: 1 } });
    expect(harness.applied).toEqual([1]);
    expect(harness.channelMessages[0]).toEqual({
      kind: 'membership_ack', sequence: 1, event_digest: '1'.repeat(64),
    });
    expect(harness.replication.snapshot()).toEqual({ cursor: 1, digest: '1'.repeat(64) });
  });

  it('detects a partition gap and requests an authoritative Hub snapshot', async () => {
    const harness = createHarness();
    harness.nextEvent = event(2, '1'.repeat(64), '2'.repeat(64));
    await expect(harness.replication.accept({ kind: 'membership_event', event: { sequence: 2 } }))
      .rejects.toThrow('peer_overlay_membership_gap');
    expect(harness.snapshots).toEqual([{ cursor: 0, digest: null }]);
    expect(harness.applied).toEqual([]);
  });

  it('falls back to the Hub when the DataChannel is unavailable', async () => {
    const harness = createHarness(false);
    await harness.replication.replicate(harness.nextEvent);
    expect(harness.hubMessages).toHaveLength(1);
    expect(harness.channelMessages).toHaveLength(0);
  });
});

function createHarness(channelReady = true) {
  const applied: number[] = [];
  const channelMessages: unknown[] = [];
  const hubMessages: unknown[] = [];
  const snapshots: Array<{ cursor: number; digest: string | null }> = [];
  const harness = {
    nextEvent: event(1, null, '1'.repeat(64)),
    applied,
    channelMessages,
    hubMessages,
    snapshots,
    replication: null as unknown as PeerOverlayControlReplication,
  };
  harness.replication = new PeerOverlayControlReplication(
    { verify: async () => harness.nextEvent },
    {
      ready: channelReady,
      sendControl: async message => { channelMessages.push(message); },
      onControl: () => () => undefined,
    },
    {
      send: async message => { hubMessages.push(message); },
      requestSnapshot: async (cursor, digest) => { snapshots.push({ cursor, digest }); },
    },
    { apply: async value => { applied.push(value.sequence); } },
    () => 1_000,
  );
  return harness;
}

function event(
  sequence: number,
  previousDigest: string | null,
  eventDigest: string,
): ValidatedMembershipEvent {
  return {
    validation: 'hub-membership-event-accepted-v1', eventId: `event-${sequence}`,
    sequence, previousDigest, eventDigest, expiresAtMs: 2_000,
    payload: { sequence, previous_digest: previousDigest, event_digest: eventDigest },
  };
}
