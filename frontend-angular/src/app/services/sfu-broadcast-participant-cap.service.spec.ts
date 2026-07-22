import { describe, expect, it } from 'vitest';

import { SfuBroadcastParticipantCapService } from './sfu-broadcast-participant-cap.service';

const ROOM = `sfu-${'a'.repeat(32)}`;

function contract(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: 'ananta.webrtc.sfu-broadcast-capacity-resolved.v1',
    profile_id: 'legacy-safe-default',
    schema_version: 1,
    profile_revision: 1,
    profile_digest: 'b'.repeat(64),
    room_id: ROOM,
    livekit_hard_cap: 250,
    active_hub_room_cap: 8,
    room_admission_cap: 8,
    angular_display_cap: 8,
    activation_state: 'legacy',
    gate_id: 'SFB-GATE-009',
    gate_passed: false,
    approval_id: null,
    ...overrides,
  };
}

describe('SfuBroadcastParticipantCapService', () => {
  it('enforces every legacy and capacity-gate boundary from the Hub profile', () => {
    const service = new SfuBroadcastParticipantCapService();
    service.install(contract());
    for (const count of [0, 1, 7, 8]) expect(() => service.enforceParticipantCount(ROOM, count)).not.toThrow();
    for (const count of [9, 10, 25, 50, 100, 250, 251]) {
      expect(() => service.enforceParticipantCount(ROOM, count)).toThrowError('capacity_cap_exceeded');
    }
    expect(() => service.enforceReceiverCount(ROOM, 7)).not.toThrow();
    expect(() => service.enforceReceiverCount(ROOM, 8)).toThrowError('capacity_cap_exceeded');
  });

  it('fails closed for cap ordering, same-revision drift, and unknown display state', () => {
    const service = new SfuBroadcastParticipantCapService();
    expect(() => service.displayCap()).toThrowError('capacity_profile_unavailable');
    expect(() => service.install(contract({ angular_display_cap: 9 })))
      .toThrowError('capacity_profile_config_drift');
    service.install(contract());
    expect(() => service.install(contract({ profile_digest: 'c'.repeat(64) })))
      .toThrowError('capacity_profile_config_drift');
  });

  it('accepts an approved candidate and only a newer explicit rollback', () => {
    const service = new SfuBroadcastParticipantCapService();
    service.install(contract({
      profile_revision: 2, profile_digest: 'c'.repeat(64), active_hub_room_cap: 10,
      room_admission_cap: 10, angular_display_cap: 10, activation_state: 'candidate',
      gate_passed: true, approval_id: 'approval-cap-10',
    }));
    expect(service.displayCap()).toBe(10);
    service.install(contract({
      profile_revision: 3, profile_digest: 'd'.repeat(64), activation_state: 'rollback',
      gate_passed: true, approval_id: 'approval-cap-10',
    }));
    expect(service.displayCap()).toBe(8);
    expect(() => service.install(contract({ profile_revision: 2 })))
      .toThrowError('capacity_profile_config_drift');
  });

  it('keeps data-destination limits outside the participant profile', () => {
    const service = new SfuBroadcastParticipantCapService();
    expect(() => service.install(contract({ data_destination_limit: 7 })))
      .toThrowError('capacity_profile_invalid');
  });
});
