import { Injectable } from '@angular/core';

import { canonicalSecurityJson, decodeB64 } from './webrtc-secure-envelope';
import { SfuBroadcastParticipantCapService } from './sfu-broadcast-participant-cap.service';

export interface GroupKeyEpochAuthorization {
  version: 1;
  authorization_id: string;
  tenant_id: string;
  room_id: string;
  publication_id: string;
  epoch: number;
  previous_epoch: number;
  member_set_digest: string;
  member_ids: string[];
  key_package_refs: Record<string, string>;
  valid_from_ms: number;
  expires_at_ms: number;
  rekey_deadline_ms: number;
  reason: 'create' | 'join' | 'leave' | 'revoke' | 'hub_failover' | 'refresh';
  hub_key_id: string;
  membership_epoch?: number;
  signature_b64: string;
}

interface ActiveGroupKey {
  authorization: GroupKeyEpochAuthorization;
  contentKey: CryptoKey;
  purgeTimer: ReturnType<typeof setTimeout> | null;
}

export class GroupKeyError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

@Injectable({ providedIn: 'root' })
export class WebrtcGroupKeyService {
  private readonly keys = new Map<string, ActiveGroupKey>();

  constructor(private readonly participantCaps: SfuBroadcastParticipantCapService | null = null) {}

  async install(
    authorization: GroupKeyEpochAuthorization,
    contentKey: CryptoKey,
    options: {
      localMemberId: string;
      hubPublicKeyB64: string;
      expectedHubKeyId: string;
      expectedMembershipEpoch?: number;
      nowMs?: number;
    },
  ): Promise<void> {
    const now = options.nowMs ?? Date.now();
    await this.verifyAuthorization(authorization, options);

    const id = this.id(authorization.room_id, authorization.publication_id);
    const current = this.keys.get(id);
    if (current && authorization.epoch <= current.authorization.epoch) {
      throw new GroupKeyError('group_epoch_stale');
    }
    if (contentKey.extractable) throw new GroupKeyError('group_content_key_extractable');
    if (!authorization.member_ids.includes(options.localMemberId)) {
      if (current) this.schedulePurge(id, current, authorization.rekey_deadline_ms - now);
      throw new GroupKeyError('member_revoked');
    }
    if (current?.purgeTimer) clearTimeout(current.purgeTimer);
    // A joining member receives only this epoch; no past key is retained.
    this.keys.set(id, { authorization, contentKey, purgeTimer: null });
  }

  async verifyAuthorization(
    authorization: GroupKeyEpochAuthorization,
    options: {
      localMemberId: string;
      hubPublicKeyB64: string;
      expectedHubKeyId: string;
      expectedMembershipEpoch?: number;
      nowMs?: number;
    },
  ): Promise<void> {
    const now = options.nowMs ?? Date.now();
    this.validateClosed(authorization);
    if (authorization.hub_key_id !== options.expectedHubKeyId) throw new GroupKeyError('hub_key_unknown');
    if (options.expectedMembershipEpoch !== undefined
        && authorization.membership_epoch !== options.expectedMembershipEpoch) {
      throw new GroupKeyError('group_membership_epoch_stale');
    }
    if (authorization.expires_at_ms <= now) throw new GroupKeyError('group_authorization_expired');
    if (authorization.rekey_deadline_ms < authorization.valid_from_ms) {
      throw new GroupKeyError('rekey_deadline_invalid');
    }
    this.participantCaps?.enforceParticipantCountIfResolved(
      authorization.room_id, authorization.member_ids.length,
    );
    const members = [...new Set(authorization.member_ids)].sort();
    if (
      members.length !== authorization.member_ids.length || members.length < 1
    ) {
      throw new GroupKeyError('member_set_invalid');
    }
    if (Object.keys(authorization.key_package_refs).sort().join('\0') !== members.join('\0')) {
      throw new GroupKeyError('key_package_set_mismatch');
    }
    if (await sha256Hex(JSON.stringify(members)) !== authorization.member_set_digest) {
      throw new GroupKeyError('member_set_digest_invalid');
    }
    const unsigned = { ...authorization } as Record<string, unknown>;
    delete unsigned['signature_b64'];
    const hubKey = await crypto.subtle.importKey(
      'raw', arrayBuffer(decodeB64(options.hubPublicKeyB64)), { name: 'Ed25519' }, false, ['verify'],
    );
    if (!await crypto.subtle.verify(
      'Ed25519', hubKey, arrayBuffer(decodeB64(authorization.signature_b64)),
      arrayBuffer(new TextEncoder().encode(canonicalSecurityJson(unsigned))),
    )) throw new GroupKeyError('group_authorization_signature_invalid');
  }

  getKey(roomId: string, publicationId: string, epoch: number, nowMs = Date.now()): CryptoKey {
    const entry = this.keys.get(this.id(roomId, publicationId));
    if (!entry || entry.authorization.epoch !== epoch) throw new GroupKeyError('group_key_missing');
    if (entry.authorization.expires_at_ms <= nowMs) {
      this.purge(roomId, publicationId);
      throw new GroupKeyError('group_key_expired');
    }
    return entry.contentKey;
  }

  purge(roomId: string, publicationId: string): void {
    const id = this.id(roomId, publicationId);
    const entry = this.keys.get(id);
    if (entry?.purgeTimer) clearTimeout(entry.purgeTimer);
    this.keys.delete(id);
  }

  clear(): void {
    for (const entry of this.keys.values()) if (entry.purgeTimer) clearTimeout(entry.purgeTimer);
    this.keys.clear();
  }

  private schedulePurge(id: string, entry: ActiveGroupKey, delayMs: number): void {
    if (entry.purgeTimer) clearTimeout(entry.purgeTimer);
    entry.purgeTimer = setTimeout(() => this.keys.delete(id), Math.max(0, delayMs));
  }

  private id(roomId: string, publicationId: string): string { return `${roomId}\0${publicationId}`; }

  private validateClosed(value: GroupKeyEpochAuthorization): void {
    const expected = [
      'version', 'authorization_id', 'tenant_id', 'room_id', 'publication_id',
      'epoch', 'previous_epoch', 'member_set_digest', 'member_ids', 'key_package_refs',
      'valid_from_ms', 'expires_at_ms', 'rekey_deadline_ms', 'reason', 'hub_key_id',
      'membership_epoch', 'signature_b64',
    ];
    const required = expected.filter(key => key !== 'membership_epoch');
    if (!value || typeof value !== 'object' || Object.keys(value).some((key) => !expected.includes(key))
      || required.some((key) => !(key in value))) throw new GroupKeyError('group_authorization_fields_invalid');
    if (value.version !== 1 || value.previous_epoch >= value.epoch || value.epoch < 1) {
      throw new GroupKeyError('group_epoch_invalid');
    }
    if (value.membership_epoch !== undefined
        && (!Number.isSafeInteger(value.membership_epoch) || value.membership_epoch < 1)) {
      throw new GroupKeyError('group_membership_epoch_invalid');
    }
  }
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

function arrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength); copy.set(value); return copy.buffer;
}
