import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { E2eEncryptionService } from './e2e-encryption.service';
import {
  SemanticSfuGroupKeyApiService,
  SfuGroupKeyDelivery,
  SfuGroupOpaquePackageUpload,
} from './semantic-sfu-group-key-api.service';
import { GroupKeyEpochAuthorization, WebrtcGroupKeyService } from './webrtc-group-key.service';
import { VerifiedPeerPackage, WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { canonicalSecurityJson, decodeB64, encodeB64 } from './webrtc-secure-envelope';
import { SfuBroadcastParticipantCapService } from './sfu-broadcast-participant-cap.service';

export interface SemanticSfuGroupContext {
  readonly hubUrl: string;
  readonly tenantId: string;
  readonly sessionId: string;
  readonly membershipEpoch: number;
  readonly localPeerId: string;
}

export interface PreparedSfuGroupEpoch {
  readonly authorization: GroupKeyEpochAuthorization & { readonly membership_epoch: number };
  readonly keyMaterial: Uint8Array;
}

export interface ReceivedSfuGroupEpoch extends PreparedSfuGroupEpoch {
  readonly publisherId: string;
  readonly packageRef: string;
}

interface OpaqueGroupPackageV1 {
  readonly version: 1;
  readonly authorization_id: string;
  readonly package_ref: string;
  readonly session_id: string;
  readonly membership_epoch: number;
  readonly group_key_epoch: number;
  readonly room_id: string;
  readonly publication_id: string;
  readonly publisher_id: string;
  readonly recipient_id: string;
  readonly nonce_b64: string;
  readonly ciphertext_b64: string;
}

@Injectable({ providedIn: 'root' })
export class SemanticSfuGroupKeyService {
  private readonly api = inject(SemanticSfuGroupKeyApiService);
  private readonly e2ee = inject(E2eEncryptionService);
  private readonly peers = inject(WebrtcPeerKeyService);
  private readonly groupKeys = inject(WebrtcGroupKeyService);
  private readonly participantCaps = inject(SfuBroadcastParticipantCapService);

  async createPublisherEpoch(
    context: SemanticSfuGroupContext,
    publicationId: string,
    memberIds: readonly string[],
  ): Promise<PreparedSfuGroupEpoch> {
    const members = normalizedMembers(memberIds, context.localPeerId, this.participantCaps);
    const peerPage = await firstValueFrom(this.api.peerPackages(context.hubUrl, context.sessionId));
    this.assertPeerPage(context, peerPage);
    const remotePackages = new Map(peerPage.packages.map(value => [value.peer_id, value]));
    for (const memberId of members) {
      if (memberId !== context.localPeerId && !remotePackages.has(memberId)) {
        throw new Error('sfu_group_peer_key_package_missing');
      }
    }
    const refs = Object.fromEntries(members.map(memberId => [
      memberId, `gkp-${context.membershipEpoch}-${crypto.randomUUID()}`,
    ]));
    const prepared = await firstValueFrom(this.api.prepareEpoch(context.hubUrl, {
      sessionId: context.sessionId,
      membershipEpoch: context.membershipEpoch,
      publicationId,
      keyPackageRefs: refs,
      idempotencyKey: `group-prepare-${crypto.randomUUID()}`,
    }));
    const authorization = prepared.authorization;
    this.assertAuthorization(context, authorization, publicationId, members);
    if (prepared.hubKeyId !== peerPage.hubKeyId || prepared.hubPublicKeyB64 !== peerPage.hubPublicKeyB64) {
      throw new Error('sfu_group_hub_key_mismatch');
    }
    const keyMaterial = crypto.getRandomValues(new Uint8Array(32));
    try {
      const contentKey = await importContentKey(keyMaterial);
      await this.groupKeys.install(authorization, contentKey, {
        localMemberId: context.localPeerId,
        hubPublicKeyB64: prepared.hubPublicKeyB64,
        expectedHubKeyId: prepared.hubKeyId,
        expectedMembershipEpoch: context.membershipEpoch,
      });
      const uploads: SfuGroupOpaquePackageUpload[] = [];
      for (const memberId of members) {
        if (memberId === context.localPeerId) continue;
        const peer = await this.peers.verifyPackage(remotePackages.get(memberId)!, {
          hubPublicKeyB64: peerPage.hubPublicKeyB64,
          expectedHubKeyId: peerPage.hubKeyId,
          expectedTenantId: context.tenantId,
          expectedScopeId: context.sessionId,
          expectedEpoch: context.membershipEpoch,
          localPeerId: context.localPeerId,
          contractDigest: peerPage.securityContractDigest,
        });
        uploads.push(await this.wrapForMember(context, authorization, peer, keyMaterial));
      }
      const delivered = await firstValueFrom(this.api.deliverPackages(
        context.hubUrl,
        authorization.authorization_id,
        `group-deliver-${crypto.randomUUID()}`,
        uploads,
      ));
      if (delivered.pendingMemberIds.length || delivered.deliveredMemberIds.length !== members.length - 1) {
        throw new Error('sfu_group_package_delivery_incomplete');
      }
      return Object.freeze({ authorization, keyMaterial });
    } catch (error) {
      this.groupKeys.purge(authorization.room_id, authorization.publication_id);
      keyMaterial.fill(0);
      throw error;
    }
  }

  async receiveAvailable(
    context: SemanticSfuGroupContext,
    cursor = '',
  ): Promise<Readonly<{ cursor: string; installed: ReceivedSfuGroupEpoch | null }>> {
    const page = await firstValueFrom(this.api.packages(
      context.hubUrl, context.sessionId, context.membershipEpoch, cursor,
    ));
    if (!page.packages.length) return Object.freeze({ cursor: page.cursor, installed: null });
    const candidates = [...page.packages]
      .filter(value => value.recipientId === context.localPeerId)
      .sort((left, right) => right.authorization.epoch - left.authorization.epoch);
    const delivery = candidates[0];
    if (!delivery) return Object.freeze({ cursor: page.cursor, installed: null });
    const peerPage = await firstValueFrom(this.api.peerPackages(context.hubUrl, context.sessionId));
    this.assertPeerPage(context, peerPage);
    const peerPackage = peerPage.packages.find(value => value.peer_id === delivery.publisherId);
    if (!peerPackage) throw new Error('sfu_group_publisher_key_package_missing');
    await this.groupKeys.verifyAuthorization(delivery.authorization, {
      localMemberId: context.localPeerId,
      hubPublicKeyB64: peerPage.hubPublicKeyB64,
      expectedHubKeyId: peerPage.hubKeyId,
      expectedMembershipEpoch: context.membershipEpoch,
    });
    this.assertAuthorization(
      context,
      delivery.authorization,
      delivery.authorization.publication_id,
      delivery.authorization.member_ids,
    );
    if (delivery.authorization.key_package_refs[context.localPeerId] !== delivery.packageRef) {
      throw new Error('sfu_group_package_reference_mismatch');
    }
    const peer = await this.peers.verifyPackage(peerPackage, {
      hubPublicKeyB64: peerPage.hubPublicKeyB64,
      expectedHubKeyId: peerPage.hubKeyId,
      expectedTenantId: context.tenantId,
      expectedScopeId: context.sessionId,
      expectedEpoch: context.membershipEpoch,
      localPeerId: context.localPeerId,
      contractDigest: peerPage.securityContractDigest,
    });
    const keyMaterial = await this.unwrapForMember(context, delivery, peer);
    try {
      await this.groupKeys.install(delivery.authorization, await importContentKey(keyMaterial), {
        localMemberId: context.localPeerId,
        hubPublicKeyB64: peerPage.hubPublicKeyB64,
        expectedHubKeyId: peerPage.hubKeyId,
        expectedMembershipEpoch: context.membershipEpoch,
      });
      return Object.freeze({
        cursor: page.cursor,
        installed: Object.freeze({
          authorization: delivery.authorization,
          keyMaterial,
          publisherId: delivery.publisherId,
          packageRef: delivery.packageRef,
        }),
      });
    } catch (error) {
      keyMaterial.fill(0);
      throw error;
    }
  }

  async acknowledge(context: SemanticSfuGroupContext, installed: ReceivedSfuGroupEpoch): Promise<void> {
    await firstValueFrom(this.api.acknowledge(
      context.hubUrl,
      installed.authorization.authorization_id,
      installed.packageRef,
      context.membershipEpoch,
    ));
  }

  status(
    context: SemanticSfuGroupContext,
    authorizationId: string,
  ): Promise<Readonly<{ acknowledgedMemberIds: readonly string[]; pendingMemberIds: readonly string[] }>> {
    return firstValueFrom(this.api.status(context.hubUrl, authorizationId));
  }

  clear(): void { this.groupKeys.clear(); }

  purge(authorization: GroupKeyEpochAuthorization): void {
    this.groupKeys.purge(authorization.room_id, authorization.publication_id);
  }

  private async wrapForMember(
    context: SemanticSfuGroupContext,
    authorization: GroupKeyEpochAuthorization & { readonly membership_epoch: number },
    peer: Readonly<VerifiedPeerPackage>,
    keyMaterial: Uint8Array,
  ): Promise<SfuGroupOpaquePackageUpload> {
    const packageRef = authorization.key_package_refs[peer.remotePeerId];
    const metadata = packageMetadata(
      context, authorization, packageRef, context.localPeerId, peer.remotePeerId,
    );
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const wrappingKey = await this.e2ee.derivePurposeAesKey(
      peer, 'sfu-group-key-wrap', authorization.authorization_id,
    );
    const ciphertext = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: arrayBuffer(nonce), additionalData: arrayBuffer(packageAad(metadata)), tagLength: 128 },
      wrappingKey,
      arrayBuffer(keyMaterial),
    );
    const envelope: OpaqueGroupPackageV1 = Object.freeze({
      ...metadata, nonce_b64: encodeB64(nonce), ciphertext_b64: encodeB64(ciphertext),
    });
    const serialized = new TextEncoder().encode(canonicalSecurityJson(envelope));
    return Object.freeze({
      recipientId: peer.remotePeerId,
      packageRef,
      opaquePackageB64: encodeB64(serialized),
      packageDigest: await sha256Hex(serialized),
      expiresAtMs: authorization.expires_at_ms,
    });
  }

  private async unwrapForMember(
    context: SemanticSfuGroupContext,
    delivery: SfuGroupKeyDelivery,
    peer: Readonly<VerifiedPeerPackage>,
  ): Promise<Uint8Array> {
    const serialized = decodeB64(delivery.opaquePackageB64);
    if (await sha256Hex(serialized) !== delivery.packageDigest) throw new Error('sfu_group_package_digest_mismatch');
    let envelope: OpaqueGroupPackageV1;
    try {
      envelope = parseOpaquePackage(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(serialized)));
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('sfu_group_')) throw error;
      throw new Error('sfu_group_opaque_package_invalid');
    }
    const expected = packageMetadata(
      context, delivery.authorization, delivery.packageRef, delivery.publisherId, context.localPeerId,
    );
    for (const [key, value] of Object.entries(expected)) {
      if (envelope[key as keyof OpaqueGroupPackageV1] !== value) {
        throw new Error('sfu_group_package_context_mismatch');
      }
    }
    if (envelope.publisher_id !== delivery.publisherId || envelope.recipient_id !== delivery.recipientId) {
      throw new Error('sfu_group_package_context_mismatch');
    }
    const wrappingKey = await this.e2ee.derivePurposeAesKey(
      peer, 'sfu-group-key-wrap', delivery.authorization.authorization_id,
    );
    try {
      const cleartext = await crypto.subtle.decrypt(
        {
          name: 'AES-GCM', iv: arrayBuffer(decodeB64(envelope.nonce_b64)),
          additionalData: arrayBuffer(packageAad(expected)), tagLength: 128,
        },
        wrappingKey,
        arrayBuffer(decodeB64(envelope.ciphertext_b64)),
      );
      const material = new Uint8Array(cleartext);
      if (material.byteLength !== 32) throw new Error('sfu_group_content_key_invalid');
      return material;
    } catch (error) {
      if (error instanceof Error && error.message === 'sfu_group_content_key_invalid') throw error;
      throw new Error('sfu_group_package_authentication_failed');
    }
  }

  private assertPeerPage(
    context: SemanticSfuGroupContext,
    page: Readonly<{ epoch: number; tenantId: string }>,
  ): void {
    if (page.epoch !== context.membershipEpoch || page.tenantId !== context.tenantId) {
      throw new Error('sfu_group_peer_package_context_mismatch');
    }
  }

  private assertAuthorization(
    context: SemanticSfuGroupContext,
    authorization: GroupKeyEpochAuthorization & { readonly membership_epoch: number },
    publicationId: string,
    members: readonly string[],
  ): void {
    if (
      authorization.tenant_id !== context.tenantId
      || authorization.publication_id !== publicationId
      || authorization.membership_epoch !== context.membershipEpoch
      || authorization.expires_at_ms <= Date.now()
      || [...authorization.member_ids].sort().join('\0') !== [...members].sort().join('\0')
    ) throw new Error('sfu_group_authorization_context_mismatch');
    this.participantCaps.enforceParticipantCountIfResolved(authorization.room_id, members.length);
  }
}

function packageMetadata(
  context: SemanticSfuGroupContext,
  authorization: GroupKeyEpochAuthorization & { readonly membership_epoch: number },
  packageRef: string,
  publisherId: string,
  recipientId: string,
) {
  return Object.freeze({
    version: 1 as const,
    authorization_id: authorization.authorization_id,
    package_ref: packageRef,
    session_id: context.sessionId,
    membership_epoch: context.membershipEpoch,
    group_key_epoch: authorization.epoch,
    room_id: authorization.room_id,
    publication_id: authorization.publication_id,
    publisher_id: publisherId,
    recipient_id: recipientId,
  });
}

function packageAad(metadata: ReturnType<typeof packageMetadata>): Uint8Array {
  return new TextEncoder().encode(canonicalSecurityJson({
    domain: 'ananta.sfu-group-key-package.v1', ...metadata,
  }));
}

function parseOpaquePackage(raw: unknown): OpaqueGroupPackageV1 {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('sfu_group_opaque_package_invalid');
  const row = raw as Record<string, unknown>;
  const keys = [
    'version', 'authorization_id', 'package_ref', 'session_id', 'membership_epoch', 'group_key_epoch',
    'room_id', 'publication_id', 'publisher_id', 'recipient_id', 'nonce_b64', 'ciphertext_b64',
  ];
  if (Object.keys(row).some(key => !keys.includes(key)) || keys.some(key => !(key in row)) || row['version'] !== 1) {
    throw new Error('sfu_group_opaque_package_invalid');
  }
  for (const key of ['authorization_id', 'package_ref', 'session_id', 'room_id', 'publication_id', 'publisher_id', 'recipient_id']) {
    if (typeof row[key] !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(row[key] as string)) {
      throw new Error('sfu_group_opaque_package_invalid');
    }
  }
  if (!Number.isSafeInteger(row['membership_epoch']) || (row['membership_epoch'] as number) < 1
      || !Number.isSafeInteger(row['group_key_epoch']) || (row['group_key_epoch'] as number) < 1
      || typeof row['nonce_b64'] !== 'string' || decodeB64(row['nonce_b64']).byteLength !== 12
      || typeof row['ciphertext_b64'] !== 'string' || decodeB64(row['ciphertext_b64']).byteLength !== 48) {
    throw new Error('sfu_group_opaque_package_invalid');
  }
  return Object.freeze(row as unknown as OpaqueGroupPackageV1);
}

function normalizedMembers(
  values: readonly string[],
  localPeerId: string,
  participantCaps: SfuBroadcastParticipantCapService,
): readonly string[] {
  participantCaps.enforceCurrentParticipantCountIfResolved(values.length);
  const members = [...new Set(values)].sort();
  if (members.length < 2 || !members.includes(localPeerId)
      || members.some(value => !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value))) {
    throw new Error('sfu_group_member_set_invalid');
  }
  return Object.freeze(members);
}

async function importContentKey(material: Uint8Array): Promise<CryptoKey> {
  if (material.byteLength !== 32) throw new Error('sfu_group_content_key_invalid');
  return crypto.subtle.importKey('raw', arrayBuffer(material), 'AES-GCM', false, ['encrypt', 'decrypt']);
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', arrayBuffer(value));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function arrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength); copy.set(value); return copy.buffer;
}
