import { PeerMeshAdmissionPolicy } from './peer-mesh-admission.policy';

describe('PeerMeshAdmissionPolicy', () => {
  const policy = new PeerMeshAdmissionPolicy(10_000);
  const base = {
    profile: 'audio_only' as const, participantCount: 4, statsReliable: true,
    cpuLimited: false, batteryConstrained: false, visible: true, uplinkBps: 4_000_000,
    requiredUplinkBps: 1_000_000, roundTripTimeMs: 30, packetLossRatio: 0.01,
    sendBufferBytes: 0, observedAtMs: 20_000,
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

  it('conservatively rejects hidden, lossy, latent and backpressured peers', () => {
    expect(policy.decide({ ...base, visible: false }, null).reasonCode)
      .toBe('peer_mesh_resource_constrained');
    expect(policy.decide({ ...base, packetLossRatio: 0.06 }, null).reasonCode)
      .toBe('peer_mesh_network_degraded');
    expect(policy.decide({ ...base, roundTripTimeMs: 351 }, null).reasonCode)
      .toBe('peer_mesh_network_degraded');
    expect(policy.decide({ ...base, sendBufferBytes: 512 * 1024 + 1 }, null).reasonCode)
      .toBe('peer_mesh_network_degraded');
  });

  it('holds recovery until hysteresis expires', () => {
    expect(policy.decide(base, 15_000).reasonCode).toBe('peer_mesh_recovery_hysteresis');
    expect(policy.decide({ ...base, observedAtMs: 25_000 }, 15_000).admitted).toBe(true);
  });
});
