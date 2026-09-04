import { Injectable, InjectionToken, inject } from '@angular/core';

export const COLLABORATION_LIVE_CLOCK = new InjectionToken<() => number>('COLLABORATION_LIVE_CLOCK', {
  providedIn: 'root',
  factory: () => () => Date.now() / 1000,
});

export interface CollaborationCursor {
  cursor_id: string;
  actor_binding_id: string;
  room_id: string;
  view_id: string;
  x: number;
  y: number;
  epoch: number;
  expires_at: number;
}

export interface RemoteControlGrant {
  grant_id: string;
  session_id: string;
  room_id: string;
  view_id: string;
  controller_actor_binding_id: string;
  controlled_actor_binding_id: string;
  revision: number;
  epoch: number;
  expires_at: number;
}

@Injectable({ providedIn: 'root' })
export class CollaborationLiveStateService {
  private readonly now = inject(COLLABORATION_LIVE_CLOCK);
  private readonly cursors = new Map<string, CollaborationCursor>();
  private followActorId: string | null = null;
  private controlGrant: RemoteControlGrant | null = null;

  upsertCursor(cursor: CollaborationCursor, context: { room_id: string; view_id: string; epoch: number }): boolean {
    if (
      cursor.room_id !== context.room_id || cursor.view_id !== context.view_id || cursor.epoch !== context.epoch ||
      cursor.expires_at <= this.now() || !Number.isFinite(cursor.x) || !Number.isFinite(cursor.y)
    ) return false;
    const current = this.cursors.get(cursor.cursor_id);
    if (current && current.actor_binding_id !== cursor.actor_binding_id) return false;
    this.cursors.set(cursor.cursor_id, { ...cursor });
    return true;
  }

  visibleCursors(roomId: string, viewId: string): CollaborationCursor[] {
    const now = this.now();
    for (const [id, cursor] of this.cursors) {
      if (cursor.expires_at <= now) this.cursors.delete(id);
    }
    return [...this.cursors.values()]
      .filter(cursor => cursor.room_id === roomId && cursor.view_id === viewId)
      .sort((left, right) => left.actor_binding_id.localeCompare(right.actor_binding_id));
  }

  startFollow(actorBindingId: string): void { this.followActorId = actorBindingId; }
  stopFollow(): void { this.followActorId = null; }
  get followedActor(): string | null { return this.followActorId; }

  applyControlGrant(
    grant: RemoteControlGrant,
    context: { session_id: string; room_id: string; view_id: string; epoch: number },
  ): boolean {
    if (
      grant.session_id !== context.session_id || grant.room_id !== context.room_id ||
      grant.view_id !== context.view_id || grant.epoch !== context.epoch || grant.expires_at <= this.now()
    ) {
      this.controlGrant = null;
      return false;
    }
    if (this.controlGrant && grant.revision <= this.controlGrant.revision) return false;
    this.controlGrant = { ...grant };
    return true;
  }

  canControl(actorBindingId: string, context: { session_id: string; room_id: string; epoch: number }): boolean {
    const grant = this.controlGrant;
    return Boolean(
      grant && grant.controller_actor_binding_id === actorBindingId && grant.session_id === context.session_id &&
      grant.room_id === context.room_id && grant.epoch === context.epoch && grant.expires_at > this.now(),
    );
  }

  revokeControl(reason: 'revoked' | 'owner_changed' | 'conflict' | 'local_stop'): string {
    this.controlGrant = null;
    return `control_${reason}`;
  }
}
