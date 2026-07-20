export type CpuBucket = 'unknown' | 'low' | 'medium' | 'high';
export type MemoryBucket = 'unknown' | 'low' | 'medium' | 'high';
export type GpuBucket = 'unknown' | 'none' | 'integrated' | 'dedicated';
export type CodecBucket = 'unknown' | 'software' | 'hardware';
export type BatteryBucket = 'unknown' | 'critical' | 'limited' | 'mains';
export type NetworkBucket = 'unknown' | 'constrained' | 'normal' | 'fast';

export interface PeerResourceProfile {
  cpu: CpuBucket;
  memory: MemoryBucket;
  gpu: GpuBucket;
  codec: CodecBucket;
  battery: BatteryBucket;
  network: NetworkBucket;
}

export interface PeerCapabilityLimits {
  cpu: CpuBucket;
  memory: MemoryBucket;
  maxDelayMs: number;
  maxArtifactBytes: number;
}

export interface CapabilityRuntimeOutcome {
  cpu: CpuBucket;
  successful: boolean;
}

export interface CapabilitySignature {
  algorithm: 'ed25519' | 'hmac-sha256';
  key_id: string;
  value: string;
}

export interface CapabilityAdvertisement {
  schema: 'ananta.semantic-capability-advertisement.v1';
  advertisement_id: string;
  session_id: string;
  room_id?: string;
  epoch: number;
  sender_id: string;
  algorithms: string[];
  roles: Array<'executor' | 'validator' | 'standby'>;
  task_types: Array<'visual_extract' | 'visual_validate' | 'speech_features' | 'speech_validate'>;
  resource_profile: PeerResourceProfile;
  measurements_expires_at_ms: number;
  expires_at_ms: number;
  max_delay_ms: number;
  max_artifact_bytes: number;
  signature: CapabilitySignature;
}

export interface CapabilityConsent {
  granted: boolean;
  version: number;
}

export const PEER_CAPABILITY_ENTROPY_BUDGET_BITS = 12;
export const PEER_CAPABILITY_TTL_MS = 60_000;
