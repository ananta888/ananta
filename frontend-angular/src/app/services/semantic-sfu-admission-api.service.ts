import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import {
  ResolvedSfuBroadcastCapacityProfile,
  SfuBroadcastParticipantCapService,
  parseResolvedSfuBroadcastCapacityProfile,
} from './sfu-broadcast-participant-cap.service';

export interface SemanticSfuConstraints {
  readonly max_bitrate_bps: number;
  readonly max_width: number;
  readonly max_height: number;
  readonly max_fps: number;
}

export interface SemanticSfuPublication {
  readonly schema: 'ananta.webrtc.media-publication.v1';
  readonly publication_id: string;
  readonly tenant_id: string;
  readonly room_id: string;
  readonly participant_id: string;
  readonly membership_epoch: number;
  readonly revision: number;
  readonly source: 'microphone' | 'camera' | 'screen';
  readonly kind: 'audio' | 'video';
  readonly privacy: 'ordinary' | 'private_recovery';
  readonly status: 'authorized';
  readonly audience_participant_id: string | null;
  readonly authorized_subscriber_ids: readonly string[];
  readonly constraints: SemanticSfuConstraints;
}

export interface SemanticSfuSubscription {
  readonly schema: 'ananta.webrtc.media-subscription.v1';
  readonly subscription_id: string;
  readonly tenant_id: string;
  readonly room_id: string;
  readonly subscriber_id: string;
  readonly publisher_id: string;
  readonly publication_id: string;
  readonly membership_epoch: number;
  readonly revision: number;
  readonly status: 'authorized';
}

export interface SemanticSfuState {
  readonly roomId: string;
  readonly membershipEpoch: number;
  readonly revision: number;
  readonly joined: boolean;
  readonly publications: readonly SemanticSfuPublication[];
  readonly subscriptions: readonly SemanticSfuSubscription[];
  readonly capacityProfile?: ResolvedSfuBroadcastCapacityProfile;
}

export interface SemanticSfuToken {
  readonly serverUrl: string;
  readonly accessToken: string;
  readonly expiresAt: number;
  readonly roomId: string;
  readonly livekitIdentity?: string;
  readonly authorizedSubscriberLivekitIdentities: Readonly<Record<string, string>>;
  readonly membershipEpoch: number;
  readonly revision: number;
  readonly publication: SemanticSfuPublication | null;
  readonly subscription: SemanticSfuSubscription | null;
  readonly capacityProfile?: ResolvedSfuBroadcastCapacityProfile;
}

export interface SemanticSfuMutationContext {
  readonly sessionId: string;
  readonly membershipEpoch: number;
  readonly expectedRevision: number;
  readonly idempotencyKey: string;
}

@Injectable({ providedIn: 'root' })
export class SemanticSfuAdmissionApiService {
  private readonly core = inject(HubApiCoreService);
  private readonly participantCaps = inject(SfuBroadcastParticipantCapService);

  state(hubUrl: string, sessionId: string, membershipEpoch: number): Observable<SemanticSfuState> {
    const base = normalizeBase(hubUrl);
    const query = new URLSearchParams({
      session_id: identifier(sessionId),
      membership_epoch: String(positiveInteger(membershipEpoch)),
    });
    return this.core.request<unknown>(
      'GET', `${base}/v1/semantic-media/sfu/admissions/state?${query.toString()}`, base,
    ).pipe(map(raw => parseSemanticSfuState(raw, this.participantCaps)));
  }

  join(
    hubUrl: string,
    context: SemanticSfuMutationContext,
    e2eeSupported: boolean,
  ): Observable<SemanticSfuToken> {
    return this.mutation(hubUrl, 'join', {
      ...mutationContext(context), strict_e2ee: true, e2ee_supported: e2eeSupported === true,
    }, raw => parseSemanticSfuToken(raw, this.participantCaps));
  }

  authorizePublication(
    hubUrl: string,
    context: SemanticSfuMutationContext,
    request: Readonly<{
      publicationId: string;
      source: 'microphone' | 'camera' | 'screen';
      kind: 'audio' | 'video';
      privacy: 'ordinary' | 'private_recovery';
      audienceParticipantId: string | null;
      authorizedSubscriberIds: readonly string[];
      constraints: SemanticSfuConstraints;
    }>,
  ): Observable<SemanticSfuToken> {
    this.participantCaps.enforceCurrentReceiverCountIfResolved(request.authorizedSubscriberIds.length);
    const subscribers = uniqueIds(request.authorizedSubscriberIds);
    return this.mutation(hubUrl, 'publications', {
      ...mutationContext(context),
      publication_id: identifier(request.publicationId),
      source: request.source,
      kind: request.kind,
      privacy: request.privacy,
      audience_participant_id: request.audienceParticipantId === null
        ? null : identifier(request.audienceParticipantId),
      authorized_subscriber_ids: subscribers,
      constraints: parseConstraints(request.constraints),
    }, raw => parseSemanticSfuToken(raw, this.participantCaps));
  }

  authorizeSubscription(
    hubUrl: string,
    context: SemanticSfuMutationContext,
    subscriptionId: string,
    publicationId: string,
  ): Observable<SemanticSfuToken> {
    return this.mutation(hubUrl, 'subscriptions', {
      ...mutationContext(context),
      subscription_id: identifier(subscriptionId),
      publication_id: identifier(publicationId),
    }, raw => parseSemanticSfuToken(raw, this.participantCaps));
  }

  leave(hubUrl: string, context: SemanticSfuMutationContext): Observable<Readonly<{
    roomId: string; revision: number; reasonCode: 'sfu_participant_left';
  }>> {
    return this.mutation(hubUrl, 'leave', mutationContext(context), raw => {
      const row = closed(raw, ['ok', 'room_id', 'revision', 'reason_code'], 'sfu_leave_response_invalid');
      if (row['ok'] !== true || row['reason_code'] !== 'sfu_participant_left') fail('sfu_leave_response_invalid');
      return Object.freeze({
        roomId: roomId(row['room_id']),
        revision: nonnegativeInteger(row['revision']),
        reasonCode: 'sfu_participant_left' as const,
      });
    });
  }

  private mutation<T>(
    hubUrl: string,
    operation: 'join' | 'publications' | 'subscriptions' | 'leave',
    body: unknown,
    parser: (raw: unknown) => T,
  ): Observable<T> {
    const base = normalizeBase(hubUrl);
    return this.core.request<unknown>(
      'POST', `${base}/v1/semantic-media/sfu/admissions/${operation}`, base, { body },
    ).pipe(map(parser));
  }
}

export function parseSemanticSfuState(
  raw: unknown,
  participantCaps?: SfuBroadcastParticipantCapService,
): SemanticSfuState {
  const row = response(raw, [
    'ok', 'room_id', 'membership_epoch', 'revision', 'joined', 'publications', 'subscriptions',
  ], ['capacity_profile'], 'sfu_state_response_invalid');
  if (row['ok'] !== true || typeof row['joined'] !== 'boolean') fail('sfu_state_response_invalid');
  const resolvedRoomId = roomId(row['room_id']);
  const capacity = capacityProfile(row, resolvedRoomId, participantCaps);
  return Object.freeze({
    roomId: resolvedRoomId,
    membershipEpoch: positiveInteger(row['membership_epoch']),
    revision: nonnegativeInteger(row['revision']),
    joined: row['joined'],
    publications: frozenArray(row['publications'], parsePublication),
    subscriptions: frozenArray(row['subscriptions'], parseSubscription),
    ...(capacity ? { capacityProfile: capacity } : {}),
  });
}

export function parseSemanticSfuToken(
  raw: unknown,
  participantCaps?: SfuBroadcastParticipantCapService,
): SemanticSfuToken {
  const row = record(raw, 'sfu_token_response_invalid');
  const required = [
    'ok', 'server_url', 'access_token', 'expires_at', 'room_id', 'membership_epoch', 'revision',
  ];
  const allowed = new Set([
    ...required,
    'livekit_identity',
    'authorized_subscriber_livekit_identities',
    'publication',
    'subscription',
    'capacity_profile',
  ]);
  if (Object.keys(row).some(key => !allowed.has(key)) || required.some(key => !(key in row))) {
    fail('sfu_token_response_invalid');
  }
  if (row['ok'] !== true || typeof row['access_token'] !== 'string' || !row['access_token']) {
    fail('sfu_token_response_invalid');
  }
  const serverUrl = String(row['server_url'] ?? '');
  if (!/^wss?:\/\/[^\s]+$/.test(serverUrl)) fail('sfu_token_response_invalid');
  const resolvedRoomId = roomId(row['room_id']);
  const capacity = capacityProfile(row, resolvedRoomId, participantCaps);
  return Object.freeze({
    serverUrl,
    accessToken: row['access_token'],
    expiresAt: positiveInteger(row['expires_at']),
    roomId: resolvedRoomId,
    ...(row['livekit_identity'] === undefined
      ? {}
      : { livekitIdentity: identifier(row['livekit_identity']) }),
    authorizedSubscriberLivekitIdentities: identifierMap(
      row['authorized_subscriber_livekit_identities'] ?? {},
    ),
    membershipEpoch: positiveInteger(row['membership_epoch']),
    revision: nonnegativeInteger(row['revision']),
    publication: row['publication'] === undefined ? null : parsePublication(row['publication']),
    subscription: row['subscription'] === undefined ? null : parseSubscription(row['subscription']),
    ...(capacity ? { capacityProfile: capacity } : {}),
  });
}

function parsePublication(raw: unknown): SemanticSfuPublication {
  const row = closed(raw, [
    'schema', 'publication_id', 'tenant_id', 'room_id', 'participant_id', 'membership_epoch', 'revision',
    'source', 'kind', 'privacy', 'status', 'audience_participant_id', 'authorized_subscriber_ids', 'constraints',
  ], 'sfu_publication_response_invalid');
  if (row['schema'] !== 'ananta.webrtc.media-publication.v1'
      || !['microphone', 'camera', 'screen'].includes(String(row['source']))
      || !['audio', 'video'].includes(String(row['kind']))
      || !['ordinary', 'private_recovery'].includes(String(row['privacy']))
      || row['status'] !== 'authorized') fail('sfu_publication_response_invalid');
  return Object.freeze({
    schema: 'ananta.webrtc.media-publication.v1',
    publication_id: identifier(row['publication_id']),
    tenant_id: identifier(row['tenant_id']),
    room_id: roomId(row['room_id']),
    participant_id: identifier(row['participant_id']),
    membership_epoch: positiveInteger(row['membership_epoch']),
    revision: positiveInteger(row['revision']),
    source: row['source'] as SemanticSfuPublication['source'],
    kind: row['kind'] as SemanticSfuPublication['kind'],
    privacy: row['privacy'] as SemanticSfuPublication['privacy'],
    status: 'authorized',
    audience_participant_id: row['audience_participant_id'] === null
      ? null : identifier(row['audience_participant_id']),
    authorized_subscriber_ids: Object.freeze(uniqueIds(array(row['authorized_subscriber_ids']))),
    constraints: parseConstraints(row['constraints']),
  });
}

function parseSubscription(raw: unknown): SemanticSfuSubscription {
  const row = closed(raw, [
    'schema', 'subscription_id', 'tenant_id', 'room_id', 'subscriber_id', 'publisher_id',
    'publication_id', 'membership_epoch', 'revision', 'status',
  ], 'sfu_subscription_response_invalid');
  if (row['schema'] !== 'ananta.webrtc.media-subscription.v1' || row['status'] !== 'authorized') {
    fail('sfu_subscription_response_invalid');
  }
  return Object.freeze({
    schema: 'ananta.webrtc.media-subscription.v1',
    subscription_id: identifier(row['subscription_id']),
    tenant_id: identifier(row['tenant_id']),
    room_id: roomId(row['room_id']),
    subscriber_id: identifier(row['subscriber_id']),
    publisher_id: identifier(row['publisher_id']),
    publication_id: identifier(row['publication_id']),
    membership_epoch: positiveInteger(row['membership_epoch']),
    revision: positiveInteger(row['revision']),
    status: 'authorized',
  });
}

function identifierMap(raw: unknown): Readonly<Record<string, string>> {
  const row = record(raw, 'sfu_identity_map_invalid');
  return Object.freeze(Object.fromEntries(
    Object.entries(row).map(([key, value]) => [identifier(key), identifier(value)]),
  ));
}

function mutationContext(value: SemanticSfuMutationContext): Record<string, unknown> {
  return {
    session_id: identifier(value.sessionId),
    membership_epoch: positiveInteger(value.membershipEpoch),
    expected_revision: nonnegativeInteger(value.expectedRevision),
    idempotency_key: identifier(value.idempotencyKey),
  };
}

function parseConstraints(raw: unknown): SemanticSfuConstraints {
  const row = closed(raw, ['max_bitrate_bps', 'max_width', 'max_height', 'max_fps'], 'sfu_constraints_invalid');
  return Object.freeze({
    max_bitrate_bps: nonnegativeInteger(row['max_bitrate_bps']),
    max_width: nonnegativeInteger(row['max_width']),
    max_height: nonnegativeInteger(row['max_height']),
    max_fps: nonnegativeInteger(row['max_fps']),
  });
}

function normalizeBase(value: string): string {
  const base = String(value || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s]+$/.test(base)) fail('sfu_hub_url_invalid');
  return base;
}

function roomId(value: unknown): string {
  if (typeof value !== 'string' || !/^sfu-[a-f0-9]{32}$/.test(value)) fail('sfu_room_id_invalid');
  return value;
}

function identifier(value: unknown): string {
  if (typeof value !== 'string' || !value.trim() || new TextEncoder().encode(value).byteLength > 128
      || /[\u0000-\u001f\u007f]/.test(value)) fail('sfu_identifier_invalid');
  return value;
}

function uniqueIds(values: readonly unknown[]): string[] {
  const ids = values.map(identifier);
  if (new Set(ids).size !== ids.length) {
    fail('sfu_identifier_list_invalid');
  }
  return [...ids].sort();
}

function capacityProfile(
  row: Record<string, unknown>,
  expectedRoomId: string,
  participantCaps?: SfuBroadcastParticipantCapService,
): ResolvedSfuBroadcastCapacityProfile | undefined {
  if (row['capacity_profile'] === undefined) return undefined;
  const profile = participantCaps
    ? participantCaps.install(row['capacity_profile'])
    : parseResolvedSfuBroadcastCapacityProfile(row['capacity_profile']);
  if (profile.roomId !== expectedRoomId) fail('capacity_profile_room_mismatch');
  return profile;
}

function positiveInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) fail('sfu_integer_invalid');
  return value as number;
}

function nonnegativeInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) fail('sfu_integer_invalid');
  return value as number;
}

function array(value: unknown): readonly unknown[] {
  if (!Array.isArray(value)) fail('sfu_array_invalid');
  return value;
}

function frozenArray<T>(raw: unknown, parser: (row: unknown) => T): readonly T[] {
  return Object.freeze(array(raw).map(parser));
}

function closed(raw: unknown, keys: readonly string[], reason: string): Record<string, unknown> {
  const value = record(raw, reason);
  if (Object.keys(value).some(key => !keys.includes(key)) || keys.some(key => !(key in value))) fail(reason);
  return value;
}

function response(
  raw: unknown,
  required: readonly string[],
  optional: readonly string[],
  reason: string,
): Record<string, unknown> {
  const value = record(raw, reason);
  const allowed = new Set([...required, ...optional]);
  if (Object.keys(value).some(key => !allowed.has(key)) || required.some(key => !(key in value))) fail(reason);
  return value;
}

function record(raw: unknown, reason: string): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) fail(reason);
  return raw as Record<string, unknown>;
}

function fail(reason: string): never { throw new Error(reason); }
