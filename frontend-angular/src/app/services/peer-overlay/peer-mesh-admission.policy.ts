export type MeshMediaProfile = 'audio_only' | 'camera_720p' | 'screenshare';

export interface MeshResourceObservation {
  readonly profile: MeshMediaProfile;
  readonly participantCount: number;
  readonly statsReliable: boolean;
  readonly cpuLimited: boolean;
  readonly batteryConstrained: boolean;
  readonly uplinkBps: number | null;
  readonly requiredUplinkBps: number;
  readonly observedAtMs: number;
}

export interface MeshAdmissionDecision {
  readonly admitted: boolean;
  readonly maxParticipants: number;
  readonly reasonCode: string;
  readonly retryAfterMs: number;
}

export class PeerMeshAdmissionPolicy {
  constructor(private readonly recoveryHoldMs = 15_000) {
    if (!Number.isSafeInteger(recoveryHoldMs) || recoveryHoldMs < 1_000) {
      throw new Error('peer_mesh_hysteresis_invalid');
    }
  }

  decide(value: MeshResourceObservation, lastDegradedAtMs: number | null): MeshAdmissionDecision {
    if (!Number.isSafeInteger(value.participantCount) || value.participantCount < 2 || value.participantCount > 4) {
      return denied(2, 'peer_mesh_hard_limit', 0);
    }
    const profileMax = value.profile === 'audio_only' ? 4 : value.profile === 'camera_720p' ? 3 : 2;
    if (!value.statsReliable || value.uplinkBps === null) return denied(2, 'peer_mesh_stats_unreliable', 0);
    if (value.cpuLimited || value.batteryConstrained) return denied(2, 'peer_mesh_resource_constrained', 0);
    if (value.uplinkBps < value.requiredUplinkBps) return denied(2, 'peer_mesh_uplink_insufficient', 0);
    if (value.participantCount > profileMax) return denied(profileMax, 'peer_mesh_profile_limit', 0);
    if (lastDegradedAtMs !== null && value.observedAtMs - lastDegradedAtMs < this.recoveryHoldMs) {
      return denied(profileMax, 'peer_mesh_recovery_hysteresis', this.recoveryHoldMs - (value.observedAtMs - lastDegradedAtMs));
    }
    return Object.freeze({ admitted: true, maxParticipants: profileMax, reasonCode: 'peer_mesh_admitted', retryAfterMs: 0 });
  }
}

function denied(maxParticipants: number, reasonCode: string, retryAfterMs: number): MeshAdmissionDecision {
  return Object.freeze({ admitted: false, maxParticipants, reasonCode, retryAfterMs });
}
