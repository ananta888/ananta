/** Payload-blind browser boundary for bounded SFU broadcast data queueing. */

export type SfuBroadcastDataTrafficClass =
  | 'interrupt'
  | 'private_recovery'
  | 'transcript_revision'
  | 'control_hint'
  | 'shared_reference';

export type SfuBroadcastAllowedTrafficKind = SfuBroadcastDataTrafficClass;

export type SfuBroadcastForbiddenTrafficKind =
  | 'authoritative_membership_mutation'
  | 'authoritative_key_mutation'
  | 'training_payload'
  | 'dataset_payload'
  | 'model_adapter_payload'
  | 'evidence_payload';

export type SfuBroadcastQueueOverflowAction =
  | 'coalesce'
  | 'drop'
  | 'layer_downshift'
  | 'disconnect';

export type SfuBroadcastQueueLimitScope =
  | 'age'
  | 'destination_class'
  | 'room'
  | 'browser_instance'
  | 'hub_send_data_adapter'
  | 'blocked_timeout';

export interface SfuBroadcastQueueLimits {
  readonly queue_bytes_max: number;
  readonly messages_max: number;
  readonly age_ms_max: number;
  readonly buffered_duration_ms_max: number;
  readonly chunk_count_max: number;
}

export interface SfuBroadcastAggregateQueueLimits {
  readonly queue_bytes_max: number;
  readonly messages_max: number;
  readonly buffered_duration_ms_max: number;
  readonly chunk_count_max: number;
}

export interface SfuBroadcastClassQueueProfile {
  readonly priority: number;
  readonly overflow_action: SfuBroadcastQueueOverflowAction;
  readonly overflow_reason_code: string;
  readonly coalesce_key_required: boolean;
  readonly limits: SfuBroadcastQueueLimits;
}

export interface SfuBroadcastDataQueueLimitsContract {
  readonly per_destination_class: Readonly<
    Record<SfuBroadcastDataTrafficClass, SfuBroadcastClassQueueProfile>
  >;
  readonly aggregate_limits: Readonly<{
    room: SfuBroadcastAggregateQueueLimits;
    browser_instance: SfuBroadcastAggregateQueueLimits;
    hub_send_data_adapter: SfuBroadcastAggregateQueueLimits;
  }>;
  readonly cleanup: Readonly<{
    sweep_interval_ms: number;
    disconnect_after_blocked_ms: number;
  }>;
}

export interface SfuBroadcastDataQueueOffer {
  readonly messageId: string;
  readonly roomHandle: string;
  readonly destinationHandle: string;
  readonly trafficKind: SfuBroadcastAllowedTrafficKind;
  readonly queueBytes: number;
  readonly bufferedDurationMs: number;
  readonly chunkCount: number;
  readonly enqueuedAtMs: number;
  readonly coalesceKey?: string;
}

export interface SfuBroadcastDataQueueDecision {
  readonly accepted: boolean;
  readonly reasonCode: string;
  readonly trafficClass?: SfuBroadcastDataTrafficClass;
  readonly overflowAction?: SfuBroadcastQueueOverflowAction;
  readonly limitScope?: SfuBroadcastQueueLimitScope;
  readonly removedMessageIds: readonly string[];
}

export interface SfuBroadcastDataQueueCleanupResult {
  readonly reasonCode?: string;
  readonly removedMessageIds: readonly string[];
  readonly disconnectRequired: boolean;
}

export interface SfuBroadcastDataQueueSnapshot {
  readonly queueBytes: number;
  readonly messages: number;
  readonly bufferedDurationMs: number;
  readonly chunkCount: number;
  readonly blockedSinceMs?: number;
  readonly disconnected: boolean;
}

/**
 * Implementations store queue metadata only. Payload bytes remain at the
 * caller-owned publish boundary and are never accepted by this interface.
 */
export interface SfuBroadcastDataQueuePort {
  enqueue(
    offer: SfuBroadcastDataQueueOffer,
    nowMs: number,
  ): SfuBroadcastDataQueueDecision;
  cleanup(nowMs: number): SfuBroadcastDataQueueCleanupResult;
  markBlocked(nowMs: number): void;
  markWritable(): void;
  resetAfterReconnect(): void;
  snapshot(): SfuBroadcastDataQueueSnapshot;
}

/**
 * Machine-readable duplicate of the deploy-time profile's bounded fields.
 * The Python contract test compares this JSON byte-independently with
 * config/sfu_broadcast_egress_limits.json to prevent browser/hub drift.
 */
export const SFU_BROADCAST_DATA_QUEUE_LIMITS_CONTRACT_JSON =
  '{"per_destination_class":{"interrupt":{"priority":0,"overflow_action":"coalesce","overflow_reason_code":"SFB_DATA_INTERRUPT_COALESCE_REQUIRED","coalesce_key_required":true,"limits":{"queue_bytes_max":16384,"messages_max":8,"age_ms_max":750,"buffered_duration_ms_max":1000,"chunk_count_max":16}},"private_recovery":{"priority":1,"overflow_action":"disconnect","overflow_reason_code":"SFB_DATA_PRIVATE_RECOVERY_DISCONNECT","coalesce_key_required":false,"limits":{"queue_bytes_max":32768,"messages_max":4,"age_ms_max":1500,"buffered_duration_ms_max":2000,"chunk_count_max":32}},"transcript_revision":{"priority":2,"overflow_action":"coalesce","overflow_reason_code":"SFB_DATA_TRANSCRIPT_COALESCE_REQUIRED","coalesce_key_required":true,"limits":{"queue_bytes_max":65536,"messages_max":16,"age_ms_max":2000,"buffered_duration_ms_max":4000,"chunk_count_max":64}},"control_hint":{"priority":3,"overflow_action":"drop","overflow_reason_code":"SFB_DATA_CONTROL_HINT_DROPPED","coalesce_key_required":false,"limits":{"queue_bytes_max":32768,"messages_max":16,"age_ms_max":1000,"buffered_duration_ms_max":2000,"chunk_count_max":32}},"shared_reference":{"priority":4,"overflow_action":"layer_downshift","overflow_reason_code":"SFB_DATA_SHARED_REFERENCE_LAYER_DOWNSHIFT","coalesce_key_required":false,"limits":{"queue_bytes_max":131072,"messages_max":32,"age_ms_max":5000,"buffered_duration_ms_max":8000,"chunk_count_max":128}}},"aggregate_limits":{"room":{"queue_bytes_max":524288,"messages_max":256,"buffered_duration_ms_max":30000,"chunk_count_max":512},"browser_instance":{"queue_bytes_max":1048576,"messages_max":512,"buffered_duration_ms_max":60000,"chunk_count_max":1024},"hub_send_data_adapter":{"queue_bytes_max":4194304,"messages_max":2048,"buffered_duration_ms_max":240000,"chunk_count_max":4096}},"cleanup":{"sweep_interval_ms":250,"disconnect_after_blocked_ms":10000}}' as const;

export const SFU_BROADCAST_FORBIDDEN_TRAFFIC_KINDS: readonly SfuBroadcastForbiddenTrafficKind[] =
  Object.freeze([
    'authoritative_membership_mutation',
    'authoritative_key_mutation',
    'training_payload',
    'dataset_payload',
    'model_adapter_payload',
    'evidence_payload',
  ]);

export const SFU_BROADCAST_DATA_QUEUE_LIMITS: SfuBroadcastDataQueueLimitsContract =
  deepFreeze(
    JSON.parse(
      SFU_BROADCAST_DATA_QUEUE_LIMITS_CONTRACT_JSON,
    ) as SfuBroadcastDataQueueLimitsContract,
  );

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const nested of Object.values(value as Record<string, unknown>)) {
      deepFreeze(nested);
    }
  }
  return value;
}
