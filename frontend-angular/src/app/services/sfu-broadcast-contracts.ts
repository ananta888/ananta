import browserCapabilitySchema from '../../../../schemas/webrtc/browser_media_capability_observation.v1.json';
import capabilityAdvertisementSchema from '../../../../schemas/webrtc/capability_advertisement.v1.json';
import fanoutAccountingSchema from '../../../../schemas/webrtc/fanout_accounting_window.v1.json';
import fanoutRouteSchema from '../../../../schemas/webrtc/fanout_route_intent.v1.json';
import groupKeyEpochSchema from '../../../../schemas/webrtc/group_key_epoch.v1.json';
import mediaPublicationSchema from '../../../../schemas/webrtc/media_publication.v1.json';
import mediaSubscriptionSchema from '../../../../schemas/webrtc/media_subscription.v1.json';
import receiverGroupSchema from '../../../../schemas/webrtc/receiver_group_intent.v1.json';
import receiverQualitySchema from '../../../../schemas/webrtc/receiver_quality_observation.v1.json';
import secureEnvelopeSchema from '../../../../schemas/webrtc/secure_envelope.v1.json';
import extensionDefinitionsSchema from '../../../../schemas/webrtc/sfu_broadcast_extension_defs.v1.json';
import layerReceiptSchema from '../../../../schemas/webrtc/sfu_layer_projection_receipt.v1.json';
import nodeObservationSchema from '../../../../schemas/webrtc/sfu_node_observation.v1.json';
import publisherProjectionSchema from '../../../../schemas/webrtc/sfu_publisher_layer_projection.v1.json';
import receiverProjectionSchema from '../../../../schemas/webrtc/sfu_receiver_layer_projection.v1.json';
import roomProjectionSchema from '../../../../schemas/webrtc/sfu_room_session_projection.v1.json';

export type SfuBroadcastJsonPrimitive = string | number | boolean | null;
export type SfuBroadcastJsonValue =
  | SfuBroadcastJsonPrimitive
  | SfuBroadcastJsonObject
  | readonly SfuBroadcastJsonValue[];
export interface SfuBroadcastJsonObject {
  readonly [key: string]: SfuBroadcastJsonValue;
}

export type SfuBroadcastContractId =
  | 'ananta.webrtc.receiver-group-intent.v1'
  | 'ananta.webrtc.fanout-route-intent.v1'
  | 'ananta.receiver-quality-observation.v1'
  | 'ananta.webrtc.fanout-accounting-window.v1'
  | 'sfu_node_observation.v1'
  | 'ananta.browser-media-capability-observation.v1'
  | 'ananta.sfu-room-session-projection.v1'
  | 'ananta.sfu-publisher-layer-projection.v1'
  | 'ananta.sfu-receiver-layer-projection.v1'
  | 'ananta.sfu-layer-projection-receipt.v1';

export interface ReceiverGroupIntentContract {
  readonly schema: 'ananta.webrtc.receiver-group-intent.v1';
  readonly group_id: string;
  readonly revision: number;
  readonly audience_snapshot_ref: string;
  readonly scope: SfuBroadcastJsonObject;
  readonly epochs: SfuBroadcastJsonObject;
  readonly member_count: number;
  readonly publications: SfuBroadcastJsonObject;
  readonly selectors: readonly SfuBroadcastJsonValue[];
  readonly policy: SfuBroadcastJsonObject;
  readonly valid_from_ms: number;
  readonly expires_at_ms: number;
  readonly limits: SfuBroadcastJsonObject;
}

export interface FanoutRouteIntentContract {
  readonly schema: 'ananta.webrtc.fanout-route-intent.v1';
  readonly schema_version: 1;
  readonly domain: 'sfu_broadcast.fanout_route_intent.v1';
  readonly route: SfuBroadcastJsonObject;
  readonly limits: SfuBroadcastJsonObject;
  readonly envelope: SfuBroadcastJsonObject;
}

export interface ReceiverQualityObservationContract {
  readonly schema: 'ananta.receiver-quality-observation.v1';
  readonly schema_version: 1;
  readonly observation_version: 'bounded-v1';
  readonly tenant_ref: string;
  readonly room_ref: string;
  readonly subscriber_ref: string;
  readonly publication_ref: string;
  readonly browser_instance_pseudonym: string;
  readonly route_epoch: number;
  readonly sequence: number;
  readonly issued_at: string;
  readonly authorization_effect: 'none';
  readonly advisory_only: true;
  readonly limits: SfuBroadcastJsonObject;
  readonly requested_layer: SfuBroadcastJsonValue;
  readonly allowed_layer: SfuBroadcastJsonValue;
  readonly effective_layer: SfuBroadcastJsonValue;
  readonly samples: readonly SfuBroadcastJsonValue[];
}

export interface FanoutAccountingWindowContract {
  readonly schema: 'ananta.webrtc.fanout-accounting-window.v1';
  readonly schema_version: 1;
  readonly domain: 'sfu_broadcast.fanout_accounting_window.v1';
  readonly accounting: SfuBroadcastJsonObject;
  readonly limits: SfuBroadcastJsonObject;
  readonly envelope: SfuBroadcastJsonObject;
}

export interface SfuNodeObservationContract {
  readonly schema_version: 'sfu_node_observation.v1';
  readonly observation: SfuBroadcastJsonObject;
  readonly proof: SfuBroadcastJsonObject;
}

export interface BrowserMediaCapabilityObservationContract {
  readonly schema: 'ananta.browser-media-capability-observation.v1';
  readonly schema_version: 1;
  readonly capability_version: 'coarse-v1';
  readonly tenant_ref: string;
  readonly room_ref: string;
  readonly admission_epoch: number;
  readonly membership_epoch: number;
  readonly browser_instance_pseudonym: string;
  readonly sequence: number;
  readonly issued_at: string;
  readonly ttl_seconds: 300;
  readonly pseudonym_rotation_seconds: 900;
  readonly capability_bucket_combinations_max: 8;
  readonly report_bytes_max: 2048;
  readonly authorization_effect: 'none';
  readonly capability_buckets: readonly SfuBroadcastJsonValue[];
}

export interface RoomSessionProjectionContract {
  readonly schema: 'ananta.sfu-room-session-projection.v1';
  readonly schema_version: 1;
  readonly domain: 'sfu_broadcast.room_session_projection.v1';
  readonly tenant_ref: string;
  readonly room_ref: string;
  readonly admission_epoch: number;
  readonly membership_epoch: number;
  readonly key_epoch: number;
  readonly topology_epoch: number;
  readonly topology: 'ordinary' | 'sfu_broadcast';
  readonly infrastructure_profile: 'ordinary_mesh_v1' | 'stock_livekit_sfu_v1' | 'authenticated_runtime_extension_v1';
  readonly session_owner_ref: string;
  readonly layer_control_mode: 'adaptive_stream' | 'manual_quality';
  readonly baseline_profile: SfuBroadcastJsonObject;
  readonly projection_version: number;
  readonly issued_at: string;
  readonly ttl_seconds: 30;
  readonly fencing: SfuBroadcastJsonObject;
  readonly construction_policy: SfuBroadcastJsonObject;
  readonly authorization_effect: 'none';
  readonly limits: SfuBroadcastJsonObject;
  readonly envelope: SfuBroadcastJsonObject;
}

export interface PublisherLayerProjectionContract {
  readonly schema: 'ananta.sfu-publisher-layer-projection.v1';
  readonly schema_version: 1;
  readonly domain: 'sfu_broadcast.publisher_layer_projection.v1';
  readonly tenant_ref: string;
  readonly room_ref: string;
  readonly publisher_ref: string;
  readonly publication_ref: string;
  readonly publication_intent_ref: string;
  readonly media_kind: 'audio' | 'video' | 'screenshare';
  readonly session_projection_version: number;
  readonly projection_version: number;
  readonly profile_version: 'publisher-layer-profile-v1';
  readonly capability_basis: SfuBroadcastJsonObject;
  readonly resolution: 'planned' | 'unknown' | 'unsupported';
  readonly safe_outcome: 'apply_projection' | 'ordinary_fallback' | 'deny';
  readonly encoding_plan: readonly SfuBroadcastJsonValue[];
  readonly hard_bounds: SfuBroadcastJsonObject;
  readonly membership_epoch: number;
  readonly route_epoch: number;
  readonly topology_epoch: number;
  readonly key_epoch: number;
  readonly issued_at: string;
  readonly ttl_seconds: 15;
  readonly fencing: SfuBroadcastJsonObject;
  readonly authorization_effect: 'narrow_only';
  readonly limits: SfuBroadcastJsonObject;
  readonly envelope: SfuBroadcastJsonObject;
}

export interface ReceiverLayerProjectionContract {
  readonly schema: 'ananta.sfu-receiver-layer-projection.v1';
  readonly schema_version: 1;
  readonly domain: 'sfu_broadcast.receiver_layer_projection.v1';
  readonly tenant_ref: string;
  readonly room_ref: string;
  readonly subscriber_ref: string;
  readonly subscription_ref: string;
  readonly publication_ref: string;
  readonly browser_instance_pseudonym: string;
  readonly session_projection_version: number;
  readonly publisher_projection_version: number;
  readonly projection_version: number;
  readonly profile_version: 'receiver-layer-profile-v1';
  readonly capability_basis: SfuBroadcastJsonObject;
  readonly quality_basis: SfuBroadcastJsonObject;
  readonly allowed_layer_corridor: SfuBroadcastJsonObject;
  readonly effective_layer: SfuBroadcastJsonValue;
  readonly resolution: 'applied' | 'unknown' | 'unsupported';
  readonly safe_outcome: 'applied_within_corridor' | 'lowest_safe_layer' | 'ordinary_fallback' | 'deny';
  readonly hysteresis_profile: SfuBroadcastJsonObject;
  readonly admission_epoch: number;
  readonly membership_epoch: number;
  readonly route_epoch: number;
  readonly topology_epoch: number;
  readonly key_epoch: number;
  readonly issued_at: string;
  readonly ttl_seconds: 10;
  readonly fencing: SfuBroadcastJsonObject;
  readonly authorization_effect: 'narrow_only';
  readonly limits: SfuBroadcastJsonObject;
  readonly envelope: SfuBroadcastJsonObject;
}

export interface LayerProjectionReceiptContract {
  readonly schema: 'ananta.sfu-layer-projection-receipt.v1';
  readonly schema_version: 1;
  readonly domain: 'sfu_broadcast.layer_projection_receipt.v1';
  readonly tenant_ref: string;
  readonly room_ref: string;
  readonly subscriber_ref: string;
  readonly subscription_ref: string;
  readonly publication_ref: string;
  readonly browser_instance_pseudonym: string;
  readonly projection_version: number;
  readonly profile_version: 'receiver-layer-profile-v1';
  readonly outcome: 'applied' | 'unsupported' | 'fallback' | 'denied';
  readonly applied_codec_class: SfuBroadcastJsonValue;
  readonly applied_encoding_class: SfuBroadcastJsonValue;
  readonly applied_rid_class: SfuBroadcastJsonValue;
  readonly applied_scalability_class: SfuBroadcastJsonValue;
  readonly applied_layer: SfuBroadcastJsonValue;
  readonly reason_code: string;
  readonly admission_epoch: number;
  readonly membership_epoch: number;
  readonly route_epoch: number;
  readonly topology_epoch: number;
  readonly key_epoch: number;
  readonly sequence: number;
  readonly issued_at: string;
  readonly ttl_seconds: 15;
  readonly authorization_effect: 'none';
  readonly limits: SfuBroadcastJsonObject;
  readonly envelope: SfuBroadcastJsonObject;
}

export type SfuBroadcastContractDocument =
  | ReceiverGroupIntentContract
  | FanoutRouteIntentContract
  | ReceiverQualityObservationContract
  | FanoutAccountingWindowContract
  | SfuNodeObservationContract
  | BrowserMediaCapabilityObservationContract
  | RoomSessionProjectionContract
  | PublisherLayerProjectionContract
  | ReceiverLayerProjectionContract
  | LayerProjectionReceiptContract;

export type ValidatedSfuBroadcastContract =
  | Readonly<{ contractId: 'ananta.webrtc.receiver-group-intent.v1'; document: ReceiverGroupIntentContract }>
  | Readonly<{ contractId: 'ananta.webrtc.fanout-route-intent.v1'; document: FanoutRouteIntentContract }>
  | Readonly<{ contractId: 'ananta.receiver-quality-observation.v1'; document: ReceiverQualityObservationContract }>
  | Readonly<{ contractId: 'ananta.webrtc.fanout-accounting-window.v1'; document: FanoutAccountingWindowContract }>
  | Readonly<{ contractId: 'sfu_node_observation.v1'; document: SfuNodeObservationContract }>
  | Readonly<{ contractId: 'ananta.browser-media-capability-observation.v1'; document: BrowserMediaCapabilityObservationContract }>
  | Readonly<{ contractId: 'ananta.sfu-room-session-projection.v1'; document: RoomSessionProjectionContract }>
  | Readonly<{ contractId: 'ananta.sfu-publisher-layer-projection.v1'; document: PublisherLayerProjectionContract }>
  | Readonly<{ contractId: 'ananta.sfu-receiver-layer-projection.v1'; document: ReceiverLayerProjectionContract }>
  | Readonly<{ contractId: 'ananta.sfu-layer-projection-receipt.v1'; document: LayerProjectionReceiptContract }>;

export type SfuBroadcastExpiryRule =
  | Readonly<{ kind: 'epoch_ms'; valuePointer: string }>
  | Readonly<{ kind: 'utc'; valuePointer: string }>
  | Readonly<{ kind: 'issued_at_fixed_ttl_ms'; valuePointer: string; fixedTtlMs: number }>
  | Readonly<{ kind: 'issued_at_ttl_ms'; valuePointer: string; ttlPointer: string }>
  | Readonly<{ kind: 'issued_at_ttl_seconds'; valuePointer: string; ttlPointer: string }>;

export interface SfuBroadcastContractDefinition {
  readonly contractId: SfuBroadcastContractId;
  readonly schemaVersion: '1';
  readonly schemaDocument: unknown;
  readonly expiry: SfuBroadcastExpiryRule;
  readonly sequencePointer: string;
  readonly epochPointers: Readonly<Record<string, string>>;
  readonly scopePointers: Readonly<Record<string, string>>;
  readonly receiverGroupDigestRequired: boolean;
}

export const SFU_BROADCAST_STRUCTURAL_LIMITS = Object.freeze({
  maxDocumentBytes: 524_288,
  maxDepth: 32,
  maxNodes: 8_192,
  maxCollectionItems: 512,
  maxStringBytes: 466_064,
  maxTotalStringBytes: 524_288,
});

export const SFU_BROADCAST_SCHEMA_DOCUMENTS: readonly unknown[] = Object.freeze([
  receiverGroupSchema,
  fanoutRouteSchema,
  receiverQualitySchema,
  fanoutAccountingSchema,
  nodeObservationSchema,
  browserCapabilitySchema,
  roomProjectionSchema,
  publisherProjectionSchema,
  receiverProjectionSchema,
  layerReceiptSchema,
  extensionDefinitionsSchema,
  secureEnvelopeSchema,
  capabilityAdvertisementSchema,
  groupKeyEpochSchema,
  mediaPublicationSchema,
  mediaSubscriptionSchema,
]);

export const SFU_BROADCAST_CONTRACT_DEFINITIONS: Readonly<Record<SfuBroadcastContractId, SfuBroadcastContractDefinition>> = Object.freeze({
  'ananta.webrtc.receiver-group-intent.v1': {
    contractId: 'ananta.webrtc.receiver-group-intent.v1', schemaVersion: '1', schemaDocument: receiverGroupSchema,
    expiry: { kind: 'epoch_ms', valuePointer: '/expires_at_ms' }, sequencePointer: '/revision',
    epochPointers: { membership_epoch: '/epochs/membership_epoch', consent_epoch: '/epochs/consent_epoch', key_epoch: '/epochs/key_epoch', audience_epoch: '/epochs/audience_epoch' },
    scopePointers: { tenant_ref: '/scope/tenant_ref', room_ref: '/scope/room_ref', consent_scope_ref: '/scope/consent_scope_ref', privacy_scope: '/scope/privacy_scope', publication_scope_ref: '/scope/publication_scope_ref' },
    receiverGroupDigestRequired: true,
  },
  'ananta.webrtc.fanout-route-intent.v1': {
    contractId: 'ananta.webrtc.fanout-route-intent.v1', schemaVersion: '1', schemaDocument: fanoutRouteSchema,
    expiry: { kind: 'issued_at_ttl_ms', valuePointer: '/route/issued_at_ms', ttlPointer: '/route/ttl_ms' }, sequencePointer: '/envelope/sequence',
    epochPointers: { route_epoch: '/route/epochs/route_epoch', topology_epoch: '/route/epochs/topology_epoch', key_epoch: '/route/epochs/key_epoch' },
    scopePointers: { tenant_ref: '/route/scope/tenant_ref', room_ref: '/route/scope/room_ref' }, receiverGroupDigestRequired: false,
  },
  'ananta.receiver-quality-observation.v1': {
    contractId: 'ananta.receiver-quality-observation.v1', schemaVersion: '1', schemaDocument: receiverQualitySchema,
    expiry: { kind: 'issued_at_fixed_ttl_ms', valuePointer: '/issued_at', fixedTtlMs: 5_000 }, sequencePointer: '/sequence',
    epochPointers: { route_epoch: '/route_epoch' }, scopePointers: { tenant_ref: '/tenant_ref', room_ref: '/room_ref', subscriber_ref: '/subscriber_ref', publication_ref: '/publication_ref', browser_instance_pseudonym: '/browser_instance_pseudonym' }, receiverGroupDigestRequired: false,
  },
  'ananta.webrtc.fanout-accounting-window.v1': {
    contractId: 'ananta.webrtc.fanout-accounting-window.v1', schemaVersion: '1', schemaDocument: fanoutAccountingSchema,
    expiry: { kind: 'epoch_ms', valuePointer: '/envelope/expires_at_ms' }, sequencePointer: '/accounting/window/sequence',
    epochPointers: { route_epoch: '/accounting/binding/route_epoch' }, scopePointers: { tenant_ref: '/accounting/binding/tenant_ref', node_ref: '/accounting/binding/node_ref', room_ref: '/accounting/binding/room_ref', publication_ref: '/accounting/binding/publication_ref', route_ref: '/accounting/binding/route_ref' }, receiverGroupDigestRequired: false,
  },
  'sfu_node_observation.v1': {
    contractId: 'sfu_node_observation.v1', schemaVersion: '1', schemaDocument: nodeObservationSchema,
    expiry: { kind: 'utc', valuePointer: '/observation/expires_at' }, sequencePointer: '/observation/sequence', epochPointers: {},
    scopePointers: { runtime_id: '/observation/scope/runtime_id', node_id: '/observation/observed_node/node_id' }, receiverGroupDigestRequired: false,
  },
  'ananta.browser-media-capability-observation.v1': {
    contractId: 'ananta.browser-media-capability-observation.v1', schemaVersion: '1', schemaDocument: browserCapabilitySchema,
    expiry: { kind: 'issued_at_ttl_seconds', valuePointer: '/issued_at', ttlPointer: '/ttl_seconds' }, sequencePointer: '/sequence',
    epochPointers: { admission_epoch: '/admission_epoch', membership_epoch: '/membership_epoch' }, scopePointers: { tenant_ref: '/tenant_ref', room_ref: '/room_ref', browser_instance_pseudonym: '/browser_instance_pseudonym' }, receiverGroupDigestRequired: false,
  },
  'ananta.sfu-room-session-projection.v1': {
    contractId: 'ananta.sfu-room-session-projection.v1', schemaVersion: '1', schemaDocument: roomProjectionSchema,
    expiry: { kind: 'issued_at_ttl_seconds', valuePointer: '/issued_at', ttlPointer: '/ttl_seconds' }, sequencePointer: '/envelope/sequence',
    epochPointers: { admission_epoch: '/admission_epoch', membership_epoch: '/membership_epoch', key_epoch: '/key_epoch', topology_epoch: '/topology_epoch' }, scopePointers: { tenant_ref: '/tenant_ref', room_ref: '/room_ref', session_owner_ref: '/session_owner_ref' }, receiverGroupDigestRequired: false,
  },
  'ananta.sfu-publisher-layer-projection.v1': {
    contractId: 'ananta.sfu-publisher-layer-projection.v1', schemaVersion: '1', schemaDocument: publisherProjectionSchema,
    expiry: { kind: 'issued_at_ttl_seconds', valuePointer: '/issued_at', ttlPointer: '/ttl_seconds' }, sequencePointer: '/envelope/sequence',
    epochPointers: { membership_epoch: '/membership_epoch', route_epoch: '/route_epoch', topology_epoch: '/topology_epoch', key_epoch: '/key_epoch' }, scopePointers: { tenant_ref: '/tenant_ref', room_ref: '/room_ref', publisher_ref: '/publisher_ref', publication_ref: '/publication_ref', publication_intent_ref: '/publication_intent_ref' }, receiverGroupDigestRequired: false,
  },
  'ananta.sfu-receiver-layer-projection.v1': {
    contractId: 'ananta.sfu-receiver-layer-projection.v1', schemaVersion: '1', schemaDocument: receiverProjectionSchema,
    expiry: { kind: 'issued_at_ttl_seconds', valuePointer: '/issued_at', ttlPointer: '/ttl_seconds' }, sequencePointer: '/envelope/sequence',
    epochPointers: { admission_epoch: '/admission_epoch', membership_epoch: '/membership_epoch', route_epoch: '/route_epoch', topology_epoch: '/topology_epoch', key_epoch: '/key_epoch' }, scopePointers: { tenant_ref: '/tenant_ref', room_ref: '/room_ref', subscriber_ref: '/subscriber_ref', subscription_ref: '/subscription_ref', publication_ref: '/publication_ref', browser_instance_pseudonym: '/browser_instance_pseudonym' }, receiverGroupDigestRequired: false,
  },
  'ananta.sfu-layer-projection-receipt.v1': {
    contractId: 'ananta.sfu-layer-projection-receipt.v1', schemaVersion: '1', schemaDocument: layerReceiptSchema,
    expiry: { kind: 'issued_at_ttl_seconds', valuePointer: '/issued_at', ttlPointer: '/ttl_seconds' }, sequencePointer: '/sequence',
    epochPointers: { admission_epoch: '/admission_epoch', membership_epoch: '/membership_epoch', route_epoch: '/route_epoch', topology_epoch: '/topology_epoch', key_epoch: '/key_epoch' }, scopePointers: { tenant_ref: '/tenant_ref', room_ref: '/room_ref', subscriber_ref: '/subscriber_ref', subscription_ref: '/subscription_ref', publication_ref: '/publication_ref', browser_instance_pseudonym: '/browser_instance_pseudonym' }, receiverGroupDigestRequired: false,
  },
});
