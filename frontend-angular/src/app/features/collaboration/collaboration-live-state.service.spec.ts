import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import {
  COLLABORATION_LIVE_CLOCK,
  CollaborationLiveStateService,
  RemoteControlGrant,
} from './collaboration-live-state.service';

function createState(clock: () => number): CollaborationLiveStateService {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ providers: [{ provide: COLLABORATION_LIVE_CLOCK, useValue: clock }] });
  return TestBed.inject(CollaborationLiveStateService);
}

describe('CollaborationLiveStateService', () => {
  it('keeps n>2 cursors actor/room/view/epoch scoped and removes expired cursors', () => {
    let now = 100;
    const state = createState(() => now);
    for (const [index, actor] of ['actor-c', 'actor-a', 'actor-b'].entries()) {
      expect(state.upsertCursor({
        cursor_id: `cursor-${actor}`,
        actor_binding_id: actor,
        room_id: 'room-a',
        view_id: 'view-a',
        x: index,
        y: index,
        epoch: 3,
        expires_at: index === 2 ? 101 : 120,
      }, { room_id: 'room-a', view_id: 'view-a', epoch: 3 })).toBe(true);
    }
    expect(state.visibleCursors('room-a', 'view-a').map(cursor => cursor.actor_binding_id)).toEqual([
      'actor-a', 'actor-b', 'actor-c',
    ]);
    now = 102;
    expect(state.visibleCursors('room-a', 'view-a').map(cursor => cursor.actor_binding_id)).toEqual([
      'actor-a', 'actor-c',
    ]);
    expect(state.upsertCursor({
      cursor_id: 'cursor-stale', actor_binding_id: 'actor-x', room_id: 'room-a', view_id: 'view-a',
      x: 0, y: 0, epoch: 2, expires_at: 120,
    }, { room_id: 'room-a', view_id: 'view-a', epoch: 3 })).toBe(false);
  });

  it('always permits local follow stop', () => {
    const state = createState(() => 100);
    state.startFollow('actor-a');
    expect(state.followedActor).toBe('actor-a');
    state.stopFollow();
    expect(state.followedActor).toBeNull();
  });

  it('requires an exact current remote-control grant and revokes on owner change', () => {
    const state = createState(() => 100);
    const grant: RemoteControlGrant = {
      grant_id: 'grant-a', session_id: 'session-a', room_id: 'room-a', view_id: 'view-a',
      controller_actor_binding_id: 'actor-a', controlled_actor_binding_id: 'actor-b', revision: 4,
      epoch: 3, expires_at: 120,
    };
    expect(state.applyControlGrant(grant, {
      session_id: 'session-a', room_id: 'room-a', view_id: 'view-a', epoch: 3,
    })).toBe(true);
    expect(state.canControl('actor-a', { session_id: 'session-a', room_id: 'room-a', epoch: 3 })).toBe(true);
    expect(state.applyControlGrant({ ...grant, revision: 3 }, {
      session_id: 'session-a', room_id: 'room-a', view_id: 'view-a', epoch: 3,
    })).toBe(false);
    expect(state.revokeControl('owner_changed')).toBe('control_owner_changed');
    expect(state.canControl('actor-a', { session_id: 'session-a', room_id: 'room-a', epoch: 3 })).toBe(false);
  });
});
