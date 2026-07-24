import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import {
  BehaviorSubject,
  NEVER,
  Observable,
  Subject,
  of,
  throwError,
} from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { KanbanApiClient } from './kanban-api.client';
import {
  KanbanEventAccumulator,
  KanbanEventBatch,
  KanbanLiveApiClient,
  KanbanLiveApiPort,
  KanbanLiveAuthPort,
  KanbanLiveEvent,
  KanbanLiveSession,
  KanbanLiveSessionConfig,
  KanbanLiveUpdate,
  KanbanSnapshot,
  normalizeKanbanSnapshot,
} from './kanban-live-events';

const TEST_CONFIG: KanbanLiveSessionConfig = {
  pollMs: 10,
  retryInitialMs: 5,
  retryMaxMs: 20,
  maxQueuedEvents: 3,
};

function snapshot(boardId: string, sequence: number): KanbanSnapshot {
  return {
    schema_version: 'kanban.snapshot.v1',
    board: {
      id: boardId,
      title: `Board ${boardId}`,
      columns: [],
    },
    cards: [],
    event_sequence: sequence,
  } as unknown as KanbanSnapshot;
}

function event(boardId: string, sequence: number, eventId = `event-${sequence}`): KanbanLiveEvent {
  return {
    schema_version: 'kanban.event.v1',
    event_id: eventId,
    board_id: boardId,
    task_id: 'task-1',
    revision: sequence,
    sequence,
    event_type: 'kanban.card.moved',
    occurred_at: '2026-07-24T10:00:00Z',
    payload: {},
  };
}

function batch(
  boardId: string,
  requested: number,
  events: KanbanLiveEvent[] = [],
  overrides: Partial<KanbanEventBatch> = {},
): KanbanEventBatch {
  const latest = events.reduce(
    (maximum, item) => Math.max(maximum, item.sequence),
    requested,
  );
  return {
    schema_version: 'kanban.event-batch.v1',
    board_id: boardId,
    requested_after_sequence: requested,
    next_after_sequence: latest,
    latest_sequence: latest,
    events,
    has_more: false,
    snapshot_required: false,
    ...overrides,
  };
}

class FakeLiveApi implements KanbanLiveApiPort {
  readonly snapshotCalls: Array<{ baseUrl: string; boardId: string }> = [];
  readonly eventCalls: Array<{ boardId: string; cursor: number; limit?: number }> = [];
  readonly snapshots: Observable<KanbanSnapshot>[] = [];
  readonly batches: Observable<KanbanEventBatch>[] = [];

  snapshot(baseUrl: string, boardId: string): Observable<KanbanSnapshot> {
    this.snapshotCalls.push({ baseUrl, boardId });
    return this.snapshots.shift() ?? NEVER;
  }

  events(
    _baseUrl: string,
    boardId: string,
    cursor: number,
    limit?: number,
  ): Observable<KanbanEventBatch> {
    this.eventCalls.push({ boardId, cursor, limit });
    return this.batches.shift() ?? NEVER;
  }
}

class FakeAuth implements KanbanLiveAuthPort {
  private readonly tokens = new BehaviorSubject<string | null>('token-a');
  readonly token$ = this.tokens.asObservable();
  readonly refreshResponses: Observable<{ access_token: string }>[] = [];
  readonly refreshToken = vi.fn(() =>
    this.refreshResponses.shift() ?? throwError(() => ({ status: 401 })),
  );

  rotate(token: string | null): void {
    this.tokens.next(token);
  }
}

function subscribeSession(
  api: FakeLiveApi,
  auth: FakeAuth,
): { updates: KanbanLiveUpdate[]; subscription: { unsubscribe(): void } } {
  const updates: KanbanLiveUpdate[] = [];
  const subscription = new KanbanLiveSession(api, auth, TEST_CONFIG)
    .connect('http://hub', 'board-a')
    .subscribe((update) => updates.push(update));
  return { updates, subscription };
}

describe('KanbanEventAccumulator', () => {
  it('deduplicates and orders an out-of-order replay batch', () => {
    const accumulator = new KanbanEventAccumulator('board-a', 8, 10);
    const result = accumulator.accept(
      batch('board-a', 10, [
        event('board-a', 12),
        event('board-a', 11),
        event('board-a', 11),
      ]),
    );

    expect(result.snapshotRequired).toBe(false);
    expect(result.events.map((item) => item.sequence)).toEqual([11, 12]);
    expect(accumulator.size).toBe(2);
    accumulator.confirm(12);
    expect(accumulator.size).toBe(0);
  });

  it('requires a snapshot for gaps, conflicting duplicates, server flags and backpressure', () => {
    expect(
      new KanbanEventAccumulator('board-a', 3, 4).accept(
        batch('board-a', 4, [event('board-a', 6)]),
      ).reason,
    ).toBe('gap');

    const duplicate = new KanbanEventAccumulator('board-a', 3, 0);
    duplicate.accept(batch('board-a', 0, [event('board-a', 1, 'first')]));
    expect(
      duplicate.accept(batch('board-a', 0, [event('board-a', 1, 'other')])).reason,
    ).toBe('duplicate');

    expect(
      new KanbanEventAccumulator('board-a', 2, 0).accept(
        batch('board-a', 0, [
          event('board-a', 1),
          event('board-a', 2),
          event('board-a', 3),
        ]),
      ).reason,
    ).toBe('overflow');

    expect(
      new KanbanEventAccumulator('board-a', 3, 0).accept(
        batch('board-a', 0, [], { snapshot_required: true }),
      ).reason,
    ).toBe('server');
  });

  it('normalizes legacy card status and always exposes the four canonical columns', () => {
    const normalized = normalizeKanbanSnapshot({
      ...snapshot('board-a', 2),
      board: {
        ...snapshot('board-a', 2).board,
        columns: [{ id: 'done', title: 'Fertig' }],
      },
      cards: [{ id: 'card-a', status: 'blocked' }] as never,
    });

    expect(normalized.board.columns.map((column) => column.id)).toEqual([
      'todo',
      'in_progress',
      'blocked',
      'completed',
    ]);
    expect(normalized.cards[0]?.column_id).toBe('blocked');
  });
});

describe('KanbanLiveSession', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('coalesces a replay burst into exactly one atomic snapshot with synchronous observables', () => {
    vi.useFakeTimers();
    const api = new FakeLiveApi();
    const auth = new FakeAuth();
    api.snapshots.push(of(snapshot('board-a', 5)), of(snapshot('board-a', 8)));
    api.batches.push(
      of(
        batch('board-a', 5, [
          event('board-a', 8),
          event('board-a', 6),
          event('board-a', 7),
        ]),
      ),
    );

    const { updates, subscription } = subscribeSession(api, auth);
    vi.advanceTimersByTime(10);

    expect(
      updates
        .filter((update) => update.kind === 'snapshot')
        .map((update) => update.snapshot.event_sequence),
    ).toEqual([5, 8]);
    expect(api.snapshotCalls).toHaveLength(2);
    subscription.unsubscribe();
  });

  it('reconnects after a transient failure using the last confirmed cursor', () => {
    vi.useFakeTimers();
    const api = new FakeLiveApi();
    const auth = new FakeAuth();
    api.snapshots.push(of(snapshot('board-a', 4)));
    api.batches.push(
      throwError(() => ({ status: 0 })),
      of(batch('board-a', 4)),
    );

    const { subscription } = subscribeSession(api, auth);
    vi.advanceTimersByTime(10);
    vi.advanceTimersByTime(5);

    expect(api.eventCalls.map((call) => call.cursor)).toEqual([4, 4]);
    subscription.unsubscribe();
  });

  it('retries a failed snapshot without advancing the replay cursor', () => {
    vi.useFakeTimers();
    const api = new FakeLiveApi();
    const auth = new FakeAuth();
    api.snapshots.push(
      throwError(() => ({ status: 0 })),
      of(snapshot('board-a', 7)),
    );
    api.batches.push(NEVER);

    const { updates, subscription } = subscribeSession(api, auth);
    vi.advanceTimersByTime(5);
    vi.advanceTimersByTime(10);

    expect(updates[0]).toMatchObject({ kind: 'error', status: 'snapshot' });
    expect(updates[1]).toMatchObject({
      kind: 'snapshot',
      snapshot: { event_sequence: 7 },
    });
    expect(api.eventCalls[0]?.cursor).toBe(7);
    subscription.unsubscribe();
  });

  it('cancels a stale generation on token rotation and all work on unsubscribe', () => {
    vi.useFakeTimers();
    const api = new FakeLiveApi();
    const auth = new FakeAuth();
    const stale = new Subject<KanbanSnapshot>();
    const current = new Subject<KanbanSnapshot>();
    api.snapshots.push(stale, current);

    const { updates, subscription } = subscribeSession(api, auth);
    expect(stale.observed).toBe(true);
    auth.rotate('token-b');
    expect(stale.observed).toBe(false);
    stale.next(snapshot('board-a', 1));
    current.next(snapshot('board-a', 2));
    expect(
      updates.filter((update) => update.kind === 'snapshot'),
    ).toHaveLength(1);

    subscription.unsubscribe();
    expect(current.observed).toBe(false);
    vi.advanceTimersByTime(100);
    expect(api.eventCalls).toHaveLength(0);
  });

  it('renews once for an auth-renewal batch and then fails closed', () => {
    vi.useFakeTimers();
    const api = new FakeLiveApi();
    const auth = new FakeAuth();
    api.snapshots.push(of(snapshot('board-a', 3)));
    api.batches.push(
      of(batch('board-a', 3, [], { auth_renewal: { required: true } })),
      of(batch('board-a', 3, [], { auth_renewal: { required: true } })),
    );
    auth.refreshResponses.push(of({ access_token: 'token-a' }));

    const { updates, subscription } = subscribeSession(api, auth);
    vi.advanceTimersByTime(10);

    expect(auth.refreshToken).toHaveBeenCalledTimes(1);
    expect(updates.at(-1)).toMatchObject({
      kind: 'error',
      status: 'authentication',
    });
    expect(api.eventCalls).toHaveLength(2);
    subscription.unsubscribe();
  });

  it('fails closed on 403 and reconnects only after an external token change', () => {
    vi.useFakeTimers();
    const api = new FakeLiveApi();
    const auth = new FakeAuth();
    api.snapshots.push(of(snapshot('board-a', 3)));
    api.batches.push(
      throwError(() => ({ status: 403 })),
      of(batch('board-a', 3)),
    );

    const { updates, subscription } = subscribeSession(api, auth);
    vi.advanceTimersByTime(10);
    vi.advanceTimersByTime(100);
    expect(api.eventCalls).toHaveLength(1);
    expect(updates.at(-1)).toMatchObject({ kind: 'error', status: 'forbidden' });

    auth.rotate('token-b');
    expect(api.eventCalls).toHaveLength(2);
    subscription.unsubscribe();
  });

  it('ends polling immediately when an authenticated user logs out', () => {
    vi.useFakeTimers();
    const api = new FakeLiveApi();
    const auth = new FakeAuth();
    api.snapshots.push(of(snapshot('board-a', 1)));
    api.batches.push(of(batch('board-a', 1)));
    const { updates, subscription } = subscribeSession(api, auth);

    auth.rotate(null);
    vi.advanceTimersByTime(100);

    expect(api.eventCalls).toHaveLength(0);
    expect(updates.at(-1)).toMatchObject({ kind: 'error', status: 'signed-out' });
    subscription.unsubscribe();
  });
});

describe('KanbanLiveApiClient', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('uses an atomic snapshot and sends both durable replay cursors', () => {
    const core = {
      get: vi.fn(() => of(snapshot('board-a', 9))),
      request: vi.fn(() => of(batch('board-a', 9))),
    };
    TestBed.configureTestingModule({
      providers: [
        KanbanLiveApiClient,
        { provide: HubApiCoreService, useValue: core },
        { provide: KanbanApiClient, useValue: {} },
      ],
    });
    const client = TestBed.inject(KanbanLiveApiClient);

    client.snapshot('http://hub/api/v1/kanban', 'board-a').subscribe();
    client.events('http://hub/api/v1/kanban', 'board-a', 9, 500).subscribe();

    expect(core.get.mock.calls[0]?.[0]).toBe(
      'http://hub/api/v1/kanban/boards/board-a/snapshot',
    );
    expect(core.request.mock.calls[0]).toEqual([
      'GET',
      'http://hub/api/v1/kanban/boards/board-a/events?after_sequence=9&limit=200',
      'http://hub/api/v1/kanban',
      { headers: { 'Last-Event-ID': '9' } },
    ]);
  });

  it('keeps older hubs compatible when the snapshot endpoint is absent', () => {
    const core = {
      get: vi.fn(() =>
        throwError(() => new HttpErrorResponse({ status: 404 })),
      ),
      request: vi.fn(),
    };
    const compatibilityApi = {
      board: vi.fn(() => of(snapshot('board-a', 0).board)),
      cards: vi.fn(() => of({ items: [], next_cursor: null })),
    };
    TestBed.configureTestingModule({
      providers: [
        KanbanLiveApiClient,
        { provide: HubApiCoreService, useValue: core },
        { provide: KanbanApiClient, useValue: compatibilityApi },
      ],
    });

    let result: KanbanSnapshot | undefined;
    TestBed.inject(KanbanLiveApiClient)
      .snapshot('http://hub', 'board-a')
      .subscribe((value) => {
        result = value;
      });

    expect(result?.event_sequence).toBe(0);
    expect(compatibilityApi.board).toHaveBeenCalledOnce();
    expect(compatibilityApi.cards).toHaveBeenCalledOnce();
  });
});
