import limitsDocument from '../../../../config/sfu_broadcast_data_limits.json';

export type SfuBroadcastDataTrafficKind =
  | 'interrupt'
  | 'private_recovery'
  | 'transcript_revision'
  | 'control_hint'
  | 'shared_reference';

export interface SfuBroadcastDataLimits {
  readonly publish: Readonly<{
    reliable_packet_bytes_max: number;
    lossy_packet_bytes_max: number;
    reliable_chunk_payload_bytes_max: number;
    lossy_chunk_payload_bytes_max: number;
    envelope_bytes_max: number;
    chunk_count_max: number;
    destination_identities_per_publish_max: number;
    batch_count_max: number;
    send_attempts_max: number;
    concurrent_messages_max: number;
  }>;
  readonly message: Readonly<{
    ttl_ms_max: number;
    plaintext_bytes_max: Readonly<Record<SfuBroadcastDataTrafficKind, number>>;
  }>;
  readonly receive: Readonly<{
    states_max: number;
    states_per_sender_max: number;
    bytes_max: number;
    bytes_per_sender_max: number;
    replay_windows_max: number;
    replay_sequences_per_window_max: number;
  }>;
  readonly lifecycle: Readonly<{
    cleanup_deadline_ms: number;
    livekit_key_indices_max: number;
  }>;
}

export const SFU_BROADCAST_DATA_LIMITS = deepFreeze(
  limitsDocument as SfuBroadcastDataLimits,
);

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const nested of Object.values(value as Record<string, unknown>)) deepFreeze(nested);
  }
  return value;
}
