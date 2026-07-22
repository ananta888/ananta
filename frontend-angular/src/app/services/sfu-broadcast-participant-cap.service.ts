import { Injectable } from '@angular/core';

export interface ResolvedSfuBroadcastCapacityProfile {
  readonly schema: 'ananta.webrtc.sfu-broadcast-capacity-resolved.v1';
  readonly profileId: string;
  readonly schemaVersion: 1;
  readonly profileRevision: number;
  readonly profileDigest: string;
  readonly roomId: string;
  readonly livekitHardCap: number;
  readonly activeHubRoomCap: number;
  readonly roomAdmissionCap: number;
  readonly angularDisplayCap: number;
  readonly activationState: 'legacy' | 'candidate' | 'rollback';
  readonly gateId: 'SFB-GATE-009';
  readonly gatePassed: boolean;
  readonly approvalId: string | null;
}

@Injectable({ providedIn: 'root' })
export class SfuBroadcastParticipantCapService {
  private readonly profiles = new Map<string, ResolvedSfuBroadcastCapacityProfile>();
  private currentRoomId: string | null = null;

  install(raw: unknown): ResolvedSfuBroadcastCapacityProfile {
    const next = parseResolvedSfuBroadcastCapacityProfile(raw);
    const current = this.profiles.get(next.roomId);
    if (current) this.assertCompatibleTransition(current, next);
    this.profiles.set(next.roomId, next);
    this.currentRoomId = next.roomId;
    return next;
  }

  hasProfile(roomId: string): boolean { return this.profiles.has(roomId); }

  hasCurrentProfile(): boolean {
    return this.currentRoomId !== null && this.profiles.has(this.currentRoomId);
  }

  displayCap(roomId?: string): number {
    return this.requireProfile(roomId).angularDisplayCap;
  }

  enforceParticipantCount(roomId: string, count: number): void {
    if (!safeCount(count) || count > this.requireProfile(roomId).roomAdmissionCap) {
      throw new Error('capacity_cap_exceeded');
    }
  }

  enforceReceiverCount(roomId: string, count: number): void {
    if (!safeCount(count) || count + 1 > this.requireProfile(roomId).roomAdmissionCap) {
      throw new Error('capacity_cap_exceeded');
    }
  }

  enforceParticipantCountIfResolved(roomId: string, count: number): void {
    if (this.hasProfile(roomId)) this.enforceParticipantCount(roomId, count);
  }

  enforceReceiverCountIfResolved(roomId: string, count: number): void {
    if (this.hasProfile(roomId)) this.enforceReceiverCount(roomId, count);
  }

  enforceCurrentParticipantCountIfResolved(count: number): void {
    if (this.hasCurrentProfile()) this.enforceParticipantCount(this.currentRoomId!, count);
  }

  enforceCurrentReceiverCountIfResolved(count: number): void {
    if (this.hasCurrentProfile()) this.enforceReceiverCount(this.currentRoomId!, count);
  }

  permitsCurrentReceiverCount(count: number): boolean {
    if (!this.hasCurrentProfile()) return true;
    return safeCount(count) && count + 1 <= this.requireProfile().roomAdmissionCap;
  }

  private requireProfile(roomId = this.currentRoomId): ResolvedSfuBroadcastCapacityProfile {
    const profile = roomId === null ? undefined : this.profiles.get(roomId);
    if (!profile) throw new Error('capacity_profile_unavailable');
    return profile;
  }

  private assertCompatibleTransition(
    current: ResolvedSfuBroadcastCapacityProfile,
    next: ResolvedSfuBroadcastCapacityProfile,
  ): void {
    if (next.profileId !== current.profileId || next.schemaVersion !== current.schemaVersion
        || next.livekitHardCap !== current.livekitHardCap || next.profileRevision < current.profileRevision) {
      throw new Error('capacity_profile_config_drift');
    }
    if (next.profileRevision === current.profileRevision) {
      if (JSON.stringify(next) !== JSON.stringify(current)) throw new Error('capacity_profile_config_drift');
      return;
    }
    if (next.activeHubRoomCap < current.activeHubRoomCap && next.activationState !== 'rollback') {
      throw new Error('capacity_profile_rollback_invalid');
    }
  }
}

export function parseResolvedSfuBroadcastCapacityProfile(raw: unknown): ResolvedSfuBroadcastCapacityProfile {
  const row = closed(raw, [
    'schema', 'profile_id', 'schema_version', 'profile_revision', 'profile_digest', 'room_id',
    'livekit_hard_cap', 'active_hub_room_cap', 'room_admission_cap', 'angular_display_cap',
    'activation_state', 'gate_id', 'gate_passed', 'approval_id',
  ]);
  if (row['schema'] !== 'ananta.webrtc.sfu-broadcast-capacity-resolved.v1'
      || row['schema_version'] !== 1 || row['gate_id'] !== 'SFB-GATE-009'
      || typeof row['gate_passed'] !== 'boolean'
      || !['legacy', 'candidate', 'rollback'].includes(String(row['activation_state']))) {
    throw new Error('capacity_profile_invalid');
  }
  const profileId = identifier(row['profile_id']);
  const roomId = identifier(row['room_id']);
  const profileRevision = positiveInteger(row['profile_revision']);
  const hard = positiveInteger(row['livekit_hard_cap']);
  const active = positiveInteger(row['active_hub_room_cap']);
  const room = positiveInteger(row['room_admission_cap']);
  const display = positiveInteger(row['angular_display_cap']);
  const approval = row['approval_id'] === null ? null : identifier(row['approval_id']);
  const state = row['activation_state'] as ResolvedSfuBroadcastCapacityProfile['activationState'];
  if (!(display <= room && room <= active && active <= hard)
      || (state === 'legacy' && (row['gate_passed'] !== false || approval !== null))
      || (state !== 'legacy' && row['gate_passed'] !== true)
      || (state === 'candidate' && approval === null)) {
    throw new Error('capacity_profile_config_drift');
  }
  const digest = String(row['profile_digest'] ?? '');
  if (!/^[a-f0-9]{64}$/.test(digest)) throw new Error('capacity_profile_invalid');
  return Object.freeze({
    schema: 'ananta.webrtc.sfu-broadcast-capacity-resolved.v1',
    profileId,
    schemaVersion: 1,
    profileRevision,
    profileDigest: digest,
    roomId,
    livekitHardCap: hard,
    activeHubRoomCap: active,
    roomAdmissionCap: room,
    angularDisplayCap: display,
    activationState: state,
    gateId: 'SFB-GATE-009',
    gatePassed: row['gate_passed'] as boolean,
    approvalId: approval,
  });
}

function safeCount(value: number): boolean { return Number.isSafeInteger(value) && value >= 0; }

function positiveInteger(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) throw new Error('capacity_profile_invalid');
  return value as number;
}

function identifier(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) {
    throw new Error('capacity_profile_invalid');
  }
  return value;
}

function closed(raw: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('capacity_profile_invalid');
  const row = raw as Record<string, unknown>;
  if (Object.keys(row).some(key => !keys.includes(key)) || keys.some(key => !(key in row))) {
    throw new Error('capacity_profile_invalid');
  }
  return row;
}
