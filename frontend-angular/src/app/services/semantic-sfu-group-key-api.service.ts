import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import { GroupKeyEpochAuthorization } from './webrtc-group-key.service';
import { SignedPeerKeyPackage } from './webrtc-peer-key.service';
import { SfuBroadcastParticipantCapService } from './sfu-broadcast-participant-cap.service';

export interface SfuGroupKeyPrepareResult {
  readonly authorization: GroupKeyEpochAuthorization & { readonly membership_epoch: number };
  readonly hubKeyId: string;
  readonly hubPublicKeyB64: string;
}

export interface SfuGroupOpaquePackageUpload {
  readonly recipientId: string;
  readonly packageRef: string;
  readonly opaquePackageB64: string;
  readonly packageDigest: string;
  readonly expiresAtMs: number;
}

export interface SfuGroupKeyDelivery {
  readonly authorization: GroupKeyEpochAuthorization & { readonly membership_epoch: number };
  readonly packageRef: string;
  readonly publisherId: string;
  readonly recipientId: string;
  readonly opaquePackageB64: string;
  readonly packageDigest: string;
  readonly expiresAtMs: number;
}

export interface SfuGroupPeerPackagePage {
  readonly epoch: number;
  readonly tenantId: string;
  readonly securityContractDigest: string;
  readonly hubKeyId: string;
  readonly hubPublicKeyB64: string;
  readonly packages: readonly SignedPeerKeyPackage[];
}

@Injectable({ providedIn: 'root' })
export class SemanticSfuGroupKeyApiService {
  private readonly core = inject(HubApiCoreService);
  private readonly participantCaps = inject(SfuBroadcastParticipantCapService);

  prepareEpoch(
    hubUrl: string,
    request: Readonly<{
      sessionId: string;
      membershipEpoch: number;
      publicationId: string;
      keyPackageRefs: Readonly<Record<string, string>>;
      idempotencyKey: string;
    }>,
  ): Observable<SfuGroupKeyPrepareResult> {
    const base = normalizeBase(hubUrl);
    this.participantCaps.enforceCurrentParticipantCountIfResolved(Object.keys(request.keyPackageRefs).length);
    return this.core.request<unknown>(
      'POST', `${base}/v1/semantic-media/sfu/group-keys/epochs`, base,
      { body: {
        session_id: identifier(request.sessionId),
        membership_epoch: positiveInteger(request.membershipEpoch),
        publication_id: identifier(request.publicationId),
        key_package_refs: identifiersRecord(request.keyPackageRefs),
        idempotency_key: identifier(request.idempotencyKey),
      } },
    ).pipe(map(raw => parsePrepare(raw, this.participantCaps)));
  }

  deliverPackages(
    hubUrl: string,
    authorizationId: string,
    idempotencyKey: string,
    packages: readonly SfuGroupOpaquePackageUpload[],
  ): Observable<Readonly<{ deliveredMemberIds: readonly string[]; pendingMemberIds: readonly string[] }>> {
    const base = normalizeBase(hubUrl);
    this.participantCaps.enforceCurrentReceiverCountIfResolved(packages.length);
    const body = {
      idempotency_key: identifier(idempotencyKey),
      packages: packages.map(value => ({
        recipient_id: identifier(value.recipientId),
        package_ref: identifier(value.packageRef),
        opaque_package_b64: base64(value.opaquePackageB64),
        package_digest: digest(value.packageDigest),
        expires_at_ms: positiveInteger(value.expiresAtMs),
      })),
    };
    return this.core.request<unknown>(
      'POST', `${base}/v1/semantic-media/sfu/group-keys/epochs/${encodeURIComponent(identifier(authorizationId))}/packages`,
      base, { body },
    ).pipe(map(raw => {
      const row = closed(raw, [
        'ok', 'authorization_id', 'delivered_member_ids', 'pending_member_ids',
      ], 'sfu_group_delivery_response_invalid');
      if (row['ok'] !== true || identifier(row['authorization_id']) !== authorizationId) {
        fail('sfu_group_delivery_response_invalid');
      }
      return Object.freeze({
        deliveredMemberIds: Object.freeze(identifierArray(row['delivered_member_ids'])),
        pendingMemberIds: Object.freeze(identifierArray(row['pending_member_ids'])),
      });
    }));
  }

  packages(
    hubUrl: string,
    sessionId: string,
    membershipEpoch: number,
    cursor = '',
  ): Observable<Readonly<{ packages: readonly SfuGroupKeyDelivery[]; cursor: string }>> {
    const base = normalizeBase(hubUrl);
    const query = new URLSearchParams({
      session_id: identifier(sessionId),
      membership_epoch: String(positiveInteger(membershipEpoch)),
      cursor: cursor ? identifier(cursor) : '',
    });
    return this.core.request<unknown>(
      'GET', `${base}/v1/semantic-media/sfu/group-keys/packages?${query.toString()}`, base,
    ).pipe(map(raw => {
      const row = closed(raw, ['ok', 'packages', 'cursor'], 'sfu_group_packages_response_invalid');
      if (row['ok'] !== true || typeof row['cursor'] !== 'string') fail('sfu_group_packages_response_invalid');
      const values = array(row['packages']).map(value => parseDelivery(value, this.participantCaps));
      return Object.freeze({ packages: Object.freeze(values), cursor: row['cursor'] });
    }));
  }

  acknowledge(
    hubUrl: string,
    authorizationId: string,
    packageRef: string,
    membershipEpoch: number,
  ): Observable<void> {
    const base = normalizeBase(hubUrl);
    return this.core.request<unknown>(
      'POST', `${base}/v1/semantic-media/sfu/group-keys/epochs/${encodeURIComponent(identifier(authorizationId))}/ack`,
      base, { body: {
        package_ref: identifier(packageRef), membership_epoch: positiveInteger(membershipEpoch),
      } },
    ).pipe(map(raw => {
      const row = closed(raw, [
        'ok', 'authorization_id', 'acknowledged_member_id',
      ], 'sfu_group_ack_response_invalid');
      if (row['ok'] !== true || identifier(row['authorization_id']) !== authorizationId) {
        fail('sfu_group_ack_response_invalid');
      }
    }));
  }

  status(
    hubUrl: string,
    authorizationId: string,
  ): Observable<Readonly<{ acknowledgedMemberIds: readonly string[]; pendingMemberIds: readonly string[] }>> {
    const base = normalizeBase(hubUrl);
    return this.core.request<unknown>(
      'GET', `${base}/v1/semantic-media/sfu/group-keys/epochs/${encodeURIComponent(identifier(authorizationId))}`, base,
    ).pipe(map(raw => {
      const row = closed(raw, [
        'ok', 'authorization_id', 'membership_epoch', 'group_key_epoch',
        'acknowledged_member_ids', 'pending_member_ids',
      ], 'sfu_group_status_response_invalid');
      if (row['ok'] !== true || identifier(row['authorization_id']) !== authorizationId) {
        fail('sfu_group_status_response_invalid');
      }
      positiveInteger(row['membership_epoch']); positiveInteger(row['group_key_epoch']);
      return Object.freeze({
        acknowledgedMemberIds: Object.freeze(identifierArray(row['acknowledged_member_ids'])),
        pendingMemberIds: Object.freeze(identifierArray(row['pending_member_ids'])),
      });
    }));
  }

  peerPackages(hubUrl: string, sessionId: string): Observable<SfuGroupPeerPackagePage> {
    const base = normalizeBase(hubUrl);
    return this.core.request<unknown>(
      'GET', `${base}/share-sessions/${encodeURIComponent(identifier(sessionId))}/security/key-packages`, base,
    ).pipe(map(parsePeerPackages));
  }
}

function parsePrepare(raw: unknown, participantCaps: SfuBroadcastParticipantCapService): SfuGroupKeyPrepareResult {
  const row = optionalClosed(raw, [
    'ok', 'authorization', 'hub_key_id', 'hub_public_key_b64',
  ], ['capacity_profile'], 'sfu_group_prepare_response_invalid');
  if (row['ok'] !== true) fail('sfu_group_prepare_response_invalid');
  if (row['capacity_profile'] !== undefined) participantCaps.install(row['capacity_profile']);
  const authorization = parseAuthorization(row['authorization'], participantCaps);
  const hubKeyId = identifier(row['hub_key_id']);
  if (authorization.hub_key_id !== hubKeyId) fail('sfu_group_prepare_response_invalid');
  return Object.freeze({
    authorization, hubKeyId, hubPublicKeyB64: base64(row['hub_public_key_b64']),
  });
}

function parseDelivery(raw: unknown, participantCaps: SfuBroadcastParticipantCapService): SfuGroupKeyDelivery {
  const row = closed(raw, [
    'kind', 'authorization', 'package_ref', 'publisher_id', 'recipient_id',
    'membership_epoch', 'opaque_package_b64', 'package_digest', 'expires_at_ms',
  ], 'sfu_group_package_response_invalid');
  if (row['kind'] !== 'sfu_group_key_package') fail('sfu_group_package_response_invalid');
  const authorization = parseAuthorization(row['authorization'], participantCaps);
  const membershipEpoch = positiveInteger(row['membership_epoch']);
  if (authorization.membership_epoch !== membershipEpoch) fail('sfu_group_package_response_invalid');
  return Object.freeze({
    authorization,
    packageRef: identifier(row['package_ref']),
    publisherId: identifier(row['publisher_id']),
    recipientId: identifier(row['recipient_id']),
    opaquePackageB64: base64(row['opaque_package_b64']),
    packageDigest: digest(row['package_digest']),
    expiresAtMs: positiveInteger(row['expires_at_ms']),
  });
}

function parseAuthorization(
  raw: unknown,
  participantCaps: SfuBroadcastParticipantCapService,
): GroupKeyEpochAuthorization & { readonly membership_epoch: number } {
  const row = closed(raw, [
    'version', 'authorization_id', 'tenant_id', 'room_id', 'publication_id', 'epoch', 'previous_epoch',
    'member_set_digest', 'member_ids', 'key_package_refs', 'valid_from_ms', 'expires_at_ms',
    'rekey_deadline_ms', 'reason', 'hub_key_id', 'membership_epoch', 'signature_b64',
  ], 'sfu_group_authorization_invalid');
  if (row['version'] !== 1 || !['create', 'join', 'leave', 'revoke', 'hub_failover', 'refresh'].includes(String(row['reason']))) {
    fail('sfu_group_authorization_invalid');
  }
  const room = identifier(row['room_id']);
  const rawMembers = array(row['member_ids']);
  participantCaps.enforceParticipantCountIfResolved(room, rawMembers.length);
  const members = rawMembers.map(identifier);
  if (members.length < 2 || new Set(members).size !== members.length) {
    fail('sfu_group_authorization_invalid');
  }
  const refs = identifiersRecord(row['key_package_refs']);
  if (Object.keys(refs).sort().join('\0') !== [...members].sort().join('\0')) fail('sfu_group_authorization_invalid');
  return Object.freeze({
    version: 1,
    authorization_id: identifier(row['authorization_id']),
    tenant_id: identifier(row['tenant_id']),
    room_id: room,
    publication_id: identifier(row['publication_id']),
    epoch: positiveInteger(row['epoch']),
    previous_epoch: nonnegativeInteger(row['previous_epoch']),
    member_set_digest: digest(row['member_set_digest']),
    member_ids: Object.freeze([...members].sort()) as unknown as string[],
    key_package_refs: Object.freeze(refs),
    valid_from_ms: positiveInteger(row['valid_from_ms']),
    expires_at_ms: positiveInteger(row['expires_at_ms']),
    rekey_deadline_ms: positiveInteger(row['rekey_deadline_ms']),
    reason: row['reason'] as GroupKeyEpochAuthorization['reason'],
    hub_key_id: identifier(row['hub_key_id']),
    membership_epoch: positiveInteger(row['membership_epoch']),
    signature_b64: base64(row['signature_b64']),
  });
}

function parsePeerPackages(raw: unknown): SfuGroupPeerPackagePage {
  const row = closed(raw, [
    'ok', 'epoch', 'tenant_id', 'security_contract_digest', 'security_contract',
    'hub_key_id', 'hub_public_key_b64', 'packages',
  ], 'sfu_group_peer_packages_invalid');
  if (row['ok'] !== true) fail('sfu_group_peer_packages_invalid');
  if (!row['security_contract'] || typeof row['security_contract'] !== 'object'
      || Array.isArray(row['security_contract'])) fail('sfu_group_peer_packages_invalid');
  const packages = array(row['packages']).map(value => parsePeerPackage(value));
  return Object.freeze({
    epoch: positiveInteger(row['epoch']),
    tenantId: identifier(row['tenant_id']),
    securityContractDigest: digest(row['security_contract_digest']),
    hubKeyId: identifier(row['hub_key_id']),
    hubPublicKeyB64: base64(row['hub_public_key_b64']),
    packages: Object.freeze(packages),
  });
}

function parsePeerPackage(raw: unknown): SignedPeerKeyPackage {
  const row = closed(raw, [
    'version', 'package_id', 'membership_id', 'membership_version', 'tenant_id', 'scope_kind', 'scope_id',
    'epoch', 'peer_id', 'recipient_peer_id', 'device_id', 'device_key_fingerprint',
    'ecdh_public_key_spki_b64', 'issued_at_ms', 'expires_at_ms', 'hub_key_id',
    'security_contract_digest', 'signature_b64',
  ], 'sfu_group_peer_package_invalid');
  if (row['version'] !== 1 || !['session', 'room'].includes(String(row['scope_kind']))) {
    fail('sfu_group_peer_package_invalid');
  }
  return Object.freeze({
    version: 1, package_id: digest(row['package_id']), membership_id: identifier(row['membership_id']),
    membership_version: positiveInteger(row['membership_version']), tenant_id: identifier(row['tenant_id']),
    scope_kind: row['scope_kind'] as 'session' | 'room', scope_id: identifier(row['scope_id']),
    epoch: positiveInteger(row['epoch']), peer_id: identifier(row['peer_id']),
    recipient_peer_id: identifier(row['recipient_peer_id']), device_id: identifier(row['device_id']),
    device_key_fingerprint: digest(row['device_key_fingerprint']),
    ecdh_public_key_spki_b64: base64(row['ecdh_public_key_spki_b64']),
    issued_at_ms: positiveInteger(row['issued_at_ms']), expires_at_ms: positiveInteger(row['expires_at_ms']),
    hub_key_id: identifier(row['hub_key_id']), security_contract_digest: digest(row['security_contract_digest']),
    signature_b64: base64(row['signature_b64']),
  });
}

function normalizeBase(value: string): string {
  const base = String(value || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s]+$/.test(base)) fail('sfu_group_hub_url_invalid');
  return base;
}

function identifiersRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('sfu_group_identifier_map_invalid');
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length < 1) {
    fail('sfu_group_identifier_map_invalid');
  }
  return Object.fromEntries(entries.map(([key, item]) => [identifier(key), identifier(item)]).sort());
}

function optionalClosed(
  raw: unknown,
  required: readonly string[],
  optional: readonly string[],
  reason: string,
): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) fail(reason);
  const row = raw as Record<string, unknown>;
  const allowed = new Set([...required, ...optional]);
  if (Object.keys(row).some(key => !allowed.has(key)) || required.some(key => !(key in row))) fail(reason);
  return row;
}

function identifierArray(value: unknown): string[] { return array(value).map(identifier); }

function identifier(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) {
    fail('sfu_group_identifier_invalid');
  }
  return value;
}

function digest(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) fail('sfu_group_digest_invalid');
  return value;
}

function base64(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length > 16_384) {
    fail('sfu_group_base64_invalid');
  }
  return value;
}

function positiveInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) fail('sfu_group_integer_invalid');
  return value as number;
}

function nonnegativeInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) fail('sfu_group_integer_invalid');
  return value as number;
}

function array(value: unknown): unknown[] {
  if (!Array.isArray(value)) fail('sfu_group_array_invalid');
  return value;
}

function closed(raw: unknown, keys: readonly string[], reason: string): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) fail(reason);
  const row = raw as Record<string, unknown>;
  if (Object.keys(row).some(key => !keys.includes(key)) || keys.some(key => !(key in row))) fail(reason);
  return row;
}

function fail(reason: string): never { throw new Error(reason); }
