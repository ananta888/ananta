import { PeerMeshAdmissionPolicy } from './peer-mesh-admission.policy';

describe('PeerMeshAdmissionPolicy', () => {
  const policy = new PeerMeshAdmissionPolicy(10_000);
  const base = {
    profile: 'audio_only' as const, participantCount: 4, statsReliable: true,
    cpuLimited: false, batteryConstrained: false, uplinkBps: 4_000_000,
    requiredUplinkBps: 1_000_000, observedAtMs: 20_000,
  };

  it('uses separate profiles, a hard maximum and conservative unknown handling', () => {
    expect(policy.decide(base, null).admitted).toBe(true);
    expect(policy.decide({ ...base, profile: 'camera_720p' }, null).reasonCode)
      .toBe('peer_mesh_profile_limit');
    expect(policy.decide({ ...base, statsReliable: false }, null).reasonCode)
      .toBe('peer_mesh_stats_unreliable');
    expect(policy.decide({ ...base, participantCount: 5 }, null).reasonCode)
      .toBe('peer_mesh_hard_limit');
  });

  it('holds recovery until hysteresis expires', () => {
    expect(policy.decide(base, 15_000).reasonCode).toBe('peer_mesh_recovery_hysteresis');
    expect(policy.decide({ ...base, observedAtMs: 25_000 }, 15_000).admitted).toBe(true);
  });
});
