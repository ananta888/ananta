import { TestBed } from '@angular/core/testing';
import { Subject, of } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { NotificationService } from '../../../services/notification.service';
import { SystemFacade } from '../../system/system.facade';
import { KanbanApiClient } from './kanban-api.client';
import {
  KanbanLiveBoardService,
  KanbanLiveUpdate,
  KanbanSnapshot,
} from './kanban-live-events';
import { KanbanStore } from './kanban.store';

function snapshot(boardId: string, sequence: number): KanbanSnapshot {
  return {
    schema_version: 'kanban.snapshot.v1',
    board: {
      id: boardId,
      title: `Board ${boardId}`,
      columns: [
        { id: 'todo', title: 'Offen' },
        { id: 'in_progress', title: 'In Arbeit' },
        { id: 'blocked', title: 'Blockiert' },
        { id: 'completed', title: 'Erledigt' },
      ],
    },
    cards: [],
    event_sequence: sequence,
  } as unknown as KanbanSnapshot;
}

describe('KanbanStore live board lifecycle', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('switches boards atomically and ignores the cancelled board generation', () => {
    const streams = new Map<string, Subject<KanbanLiveUpdate>>();
    const live = {
      connect: vi.fn((_baseUrl: string, boardId: string) => {
        const stream = new Subject<KanbanLiveUpdate>();
        streams.set(boardId, stream);
        return stream;
      }),
    };
    const api = {
      boards: vi.fn(() =>
        of({
          items: [
            { id: 'board-a', title: 'Board A' },
            { id: 'board-b', title: 'Board B' },
          ],
          next_cursor: null,
        }),
      ),
    };
    TestBed.configureTestingModule({
      providers: [
        KanbanStore,
        { provide: KanbanApiClient, useValue: api },
        { provide: KanbanLiveBoardService, useValue: live },
        {
          provide: SystemFacade,
          useValue: {
            resolveHubAgent: () => ({ url: 'http://hub' }),
          },
        },
        {
          provide: NotificationService,
          useValue: { success: vi.fn(), error: vi.fn() },
        },
      ],
    });
    const store = TestBed.inject(KanbanStore);

    store.load();
    const first = streams.get('board-a');
    first?.next({ kind: 'snapshot', snapshot: snapshot('board-a', 1) });
    expect(store.board()?.id).toBe('board-a');

    store.selectBoard('board-b');
    expect(first?.observed).toBe(false);
    first?.next({ kind: 'snapshot', snapshot: snapshot('board-a', 99) });
    streams
      .get('board-b')
      ?.next({ kind: 'snapshot', snapshot: snapshot('board-b', 2) });

    expect(store.board()?.id).toBe('board-b');
    expect(store.loading()).toBe(false);
  });

  it('surfaces fail-closed state and releases the route subscription on destroy', () => {
    const stream = new Subject<KanbanLiveUpdate>();
    TestBed.configureTestingModule({
      providers: [
        KanbanStore,
        {
          provide: KanbanApiClient,
          useValue: {
            boards: () =>
              of({
                items: [{ id: 'board-a', title: 'Board A' }],
                next_cursor: null,
              }),
          },
        },
        {
          provide: KanbanLiveBoardService,
          useValue: { connect: () => stream },
        },
        {
          provide: SystemFacade,
          useValue: {
            resolveHubAgent: () => ({ url: 'http://hub' }),
          },
        },
        {
          provide: NotificationService,
          useValue: { success: vi.fn(), error: vi.fn() },
        },
      ],
    });
    const store = TestBed.inject(KanbanStore);
    store.load();
    stream.next({
      kind: 'error',
      status: 'forbidden',
      message: 'Keine Berechtigung.',
    });

    expect(store.error()).toBe('Keine Berechtigung.');
    expect(stream.observed).toBe(true);
    TestBed.resetTestingModule();
    expect(stream.observed).toBe(false);
  });
});
