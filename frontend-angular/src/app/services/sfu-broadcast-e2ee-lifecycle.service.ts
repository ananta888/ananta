import { Inject, Injectable } from '@angular/core';

import {
  SFU_BROADCAST_GROUP_KEY_PORT,
  type SfuBroadcastCryptographicScope,
  type SfuBroadcastGroupKeyLease,
  type SfuBroadcastGroupKeyPort,
} from './sfu-broadcast-group-key.port';
import type { SfuKeyPort, SfuLifecyclePort } from './sfu-room-session.ports';

export interface SfuStrictE2eeSessionPort {
  readonly lifecycle: SfuLifecyclePort;
  readonly key: SfuKeyPort;
}

export interface SfuBroadcastE2eeLifecycleView {
  readonly state: 'idle' | 'arming' | 'active' | 'fenced' | 'destroyed';
  readonly membershipEpoch: number | null;
  readonly routeEpoch: number | null;
  readonly keyEpoch: number | null;
  readonly reasonCode: string;
}

@Injectable({ providedIn: 'root' })
export class SfuBroadcastE2eeLifecycleService {
  private lease: SfuBroadcastGroupKeyLease | null = null;
  private activeScope: Readonly<SfuBroadcastCryptographicScope> | null = null;
  private state: SfuBroadcastE2eeLifecycleView = frozenView('idle', null, 'sfu_e2ee_not_armed');
  private operation = 0;

  constructor(
    @Inject(SFU_BROADCAST_GROUP_KEY_PORT) private readonly keys: SfuBroadcastGroupKeyPort,
  ) {}

  snapshot(): SfuBroadcastE2eeLifecycleView { return this.state; }

  async activate(
    scope: Readonly<SfuBroadcastCryptographicScope>,
    session: SfuStrictE2eeSessionPort,
    nowMs = Date.now(),
  ): Promise<void> {
    validateScope(scope);
    if (!session.lifecycle.e2eeSupported || !session.key.rotateKeyAtEpoch) {
      throw new Error('sfu_e2ee_sdk_path_unsupported');
    }
    if (this.state.state === 'destroyed') throw new Error('sfu_e2ee_lifecycle_destroyed');
    const previous = this.activeScope;
    if (previous) validateSuccessor(previous, scope);
    const serial = ++this.operation;
    this.state = frozenView('arming', scope, 'sfu_e2ee_key_arming');
    let nextLease: SfuBroadcastGroupKeyLease | null = null;
    try {
      nextLease = await this.keys.acquire(scope, nowMs);
      validateLease(nextLease, scope, nowMs, true);
      if (!nextLease.withLivekitKeyMaterial) throw new Error('sfu_e2ee_livekit_material_unavailable');
      await nextLease.withLivekitKeyMaterial(async material => {
        const owned = Uint8Array.from(material);
        try {
          if (owned.byteLength !== 32) throw new Error('sfu_e2ee_key_invalid');
          await session.key.rotateKeyAtEpoch!(owned, scope.keyEpoch);
        } finally {
          owned.fill(0);
        }
      });
      if (serial !== this.operation) throw new Error('sfu_e2ee_operation_fenced');
      this.lease?.release();
      this.lease = nextLease;
      this.activeScope = Object.freeze({ ...scope });
      this.state = frozenView('active', scope, 'sfu_e2ee_active');
    } catch (error) {
      nextLease?.release();
      this.lease = null;
      this.activeScope = null;
      this.state = frozenView('fenced', scope, reason(error));
      await session.lifecycle.disconnect().catch(() => undefined);
      throw error;
    }
  }

  guard(scope: Readonly<SfuBroadcastCryptographicScope>, nowMs = Date.now()): void {
    const active = this.activeScope;
    const lease = this.lease;
    if (this.state.state !== 'active' || !active || !lease) throw new Error('sfu_e2ee_not_active');
    if (!sameScope(active, scope)) throw new Error('sfu_e2ee_scope_stale');
    if (lease.expiresAtMs <= nowMs) {
      this.fence('sfu_e2ee_key_expired');
      throw new Error('sfu_e2ee_key_expired');
    }
  }

  async revoke(session: SfuStrictE2eeSessionPort, reasonCode = 'sfu_e2ee_revoked'): Promise<void> {
    this.fence(reasonCode);
    await session.lifecycle.disconnect();
  }

  async destroy(session: SfuStrictE2eeSessionPort): Promise<void> {
    ++this.operation;
    this.releaseLease();
    this.activeScope = null;
    this.state = frozenView('destroyed', null, 'sfu_e2ee_destroyed');
    await session.lifecycle.destroy();
  }

  private fence(reasonCode: string): void {
    ++this.operation;
    const previous = this.activeScope;
    this.releaseLease();
    this.activeScope = null;
    this.state = frozenView('fenced', previous, reasonCode);
  }

  private releaseLease(): void {
    try { this.lease?.release(); } finally { this.lease = null; }
  }
}

function validateLease(
  lease: SfuBroadcastGroupKeyLease,
  scope: Readonly<SfuBroadcastCryptographicScope>,
  nowMs: number,
  requireLivekitMaterial: boolean,
): void {
  if (!sameScope(lease.scope, scope) || lease.expiresAtMs <= nowMs
      || !identifier(lease.authorizationRef) || !identifier(lease.keyId)) {
    throw new Error('sfu_group_key_lease_context_invalid');
  }
  if (lease.contentKey.type !== 'secret' || lease.contentKey.algorithm.name !== 'AES-GCM'
      || lease.contentKey.extractable
      || !lease.contentKey.usages.includes('encrypt')
      || !lease.contentKey.usages.includes('decrypt')) {
    throw new Error('sfu_group_key_lease_key_invalid');
  }
  if (requireLivekitMaterial && !lease.withLivekitKeyMaterial) {
    throw new Error('sfu_e2ee_livekit_material_unavailable');
  }
}

export function validateSfuBroadcastGroupKeyLease(
  lease: SfuBroadcastGroupKeyLease,
  scope: Readonly<SfuBroadcastCryptographicScope>,
  nowMs: number,
): void {
  validateLease(lease, scope, nowMs, false);
}

function validateScope(value: Readonly<SfuBroadcastCryptographicScope>): void {
  const ids = [
    value.tenantRef, value.roomRef, value.publicationRef, value.audienceRef,
    value.localHandle, value.fencingToken,
  ];
  const epochs = [value.membershipEpoch, value.routeEpoch, value.keyEpoch];
  if (ids.some(item => !identifier(item))
      || epochs.some(item => !Number.isSafeInteger(item) || item < 1)) {
    throw new Error('sfu_e2ee_scope_invalid');
  }
}

function validateSuccessor(
  current: Readonly<SfuBroadcastCryptographicScope>,
  next: Readonly<SfuBroadcastCryptographicScope>,
): void {
  if (current.tenantRef !== next.tenantRef || current.roomRef !== next.roomRef
      || current.publicationRef !== next.publicationRef || current.audienceRef !== next.audienceRef
      || current.localHandle !== next.localHandle) {
    throw new Error('sfu_e2ee_room_recreation_required');
  }
  if (next.keyEpoch <= current.keyEpoch || next.routeEpoch < current.routeEpoch
      || next.membershipEpoch < current.membershipEpoch
      || next.fencingToken === current.fencingToken) {
    throw new Error('sfu_e2ee_epoch_stale');
  }
}

function sameScope(
  left: Readonly<SfuBroadcastCryptographicScope>,
  right: Readonly<SfuBroadcastCryptographicScope>,
): boolean {
  return left.tenantRef === right.tenantRef && left.roomRef === right.roomRef
    && left.publicationRef === right.publicationRef && left.audienceRef === right.audienceRef
    && left.localHandle === right.localHandle && left.membershipEpoch === right.membershipEpoch
    && left.routeEpoch === right.routeEpoch && left.keyEpoch === right.keyEpoch
    && left.fencingToken === right.fencingToken;
}

function frozenView(
  state: SfuBroadcastE2eeLifecycleView['state'],
  scope: Readonly<SfuBroadcastCryptographicScope> | null,
  reasonCode: string,
): SfuBroadcastE2eeLifecycleView {
  return Object.freeze({
    state,
    membershipEpoch: scope?.membershipEpoch ?? null,
    routeEpoch: scope?.routeEpoch ?? null,
    keyEpoch: scope?.keyEpoch ?? null,
    reasonCode,
  });
}

function identifier(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
}

function reason(error: unknown): string {
  return error instanceof Error && identifier(error.message) ? error.message : 'sfu_e2ee_activation_failed';
}
