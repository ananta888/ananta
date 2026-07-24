import { HttpErrorResponse } from '@angular/common/http';
import { Injectable, InjectionToken, inject } from '@angular/core';
import {
  Observable,
  Subscription,
  catchError,
  forkJoin,
  map,
  throwError,
} from 'rxjs';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { UserAuthService } from '../../../services/user-auth.service';
import {
  KanbanApiClient,
  KanbanBoard,
  KanbanCard,
  KanbanColumnId,
} from './kanban-api.client';

export interface KanbanSnapshot {
  schema_version: 'kanban.snapshot.v1';
  board: KanbanBoard;
  cards: KanbanCard[];
  event_sequence: number;
}

export interface KanbanLiveEvent {
  schema_version: 'kanban.event.v1';
  event_id: string;
  board_id: string;
  task_id: string;
  revision: number;
  sequence: number;
  event_type: `kanban.${string}`;
  occurred_at: string;
  payload: Record<string, unknown>;
}

export interface KanbanEventBatch {
  schema_version: 'kanban.event-batch.v1';
  board_id: string;
  requested_after_sequence: number;
  next_after_sequence: number;
  latest_sequence: number;
  events: KanbanLiveEvent[];
  has_more: boolean;
  overflow?: boolean;
  overflow_reason?: string | null;
  gap_detected?: boolean;
  gap_after_sequence?: number | null;
  snapshot_required?: boolean;
  snapshot_url?: string | null;
  auth_renewal?: {
    required?: boolean;
    reason?: string | null;
  } | null;
}

export interface KanbanLiveApiPort {
  snapshot(baseUrl: string, boardId: string): Observable<KanbanSnapshot>;
  events(
    baseUrl: string,
    boardId: string,
    afterSequence: number,
    limit?: number,
  ): Observable<KanbanEventBatch>;
}

export interface KanbanLiveAuthPort {
  readonly token$: Observable<string | null>;
  refreshToken(): Observable<{ access_token: string; refresh_token?: string }>;
}

export type KanbanLiveUpdate =
  | { kind: 'snapshot'; snapshot: KanbanSnapshot }
  | {
      kind: 'error';
      status: 'authentication' | 'forbidden' | 'signed-out' | 'snapshot';
      message: string;
    };

export interface KanbanAccumulatorResult {
  events: readonly KanbanLiveEvent[];
  snapshotRequired: boolean;
  reason?: 'board' | 'cursor' | 'duplicate' | 'gap' | 'overflow' | 'server';
}

const CANONICAL_COLUMN_IDS: readonly KanbanColumnId[] = [
  'todo',
  'in_progress',
  'blocked',
  'completed',
];

const COLUMN_TITLES: Record<KanbanColumnId, string> = {
  todo: 'Offen',
  in_progress: 'In Arbeit',
  blocked: 'Blockiert',
  completed: 'Erledigt',
};

function isCanonicalColumnId(value: unknown): value is KanbanColumnId {
  return (
    typeof value === 'string' &&
    (CANONICAL_COLUMN_IDS as readonly string[]).includes(value)
  );
}

export function normalizeKanbanSnapshot(snapshot: KanbanSnapshot): KanbanSnapshot {
  const rawBoard = snapshot.board as KanbanBoard & {
    columns?: Array<Record<string, unknown>>;
  };
  const configuredColumns = new Map(
    (rawBoard.columns ?? [])
      .filter((column) => isCanonicalColumnId(column['id']))
      .map((column) => [column['id'] as KanbanColumnId, column]),
  );
  const columns = CANONICAL_COLUMN_IDS.map((id) => ({
    ...(configuredColumns.get(id) ?? {}),
    id,
    title:
      typeof configuredColumns.get(id)?.['title'] === 'string'
        ? configuredColumns.get(id)?.['title']
        : COLUMN_TITLES[id],
  }));
  const cards = snapshot.cards.map((card) => {
    const compatibleCard = card as KanbanCard & { status?: unknown };
    const compatibleStatus =
      compatibleCard.status === 'done'
        ? 'completed'
        : compatibleCard.status === 'blocked_by_dependency'
          ? 'blocked'
          : compatibleCard.status;
    const columnId = isCanonicalColumnId(compatibleCard.column_id)
      ? compatibleCard.column_id
      : isCanonicalColumnId(compatibleStatus)
        ? compatibleStatus
        : 'todo';
    return { ...card, column_id: columnId };
  });

  return {
    ...snapshot,
    board: { ...snapshot.board, columns } as KanbanBoard,
    cards,
    event_sequence: Math.max(0, Math.trunc(snapshot.event_sequence)),
  };
}

export class KanbanEventAccumulator {
  private readonly queued = new Map<number, KanbanLiveEvent>();
  private confirmedSequence: number;

  constructor(
    private readonly boardId: string,
    private readonly maxQueuedEvents = 128,
    confirmedSequence = 0,
  ) {
    this.confirmedSequence = Math.max(0, Math.trunc(confirmedSequence));
  }

  get size(): number {
    return this.queued.size;
  }

  reset(confirmedSequence: number): void {
    this.confirmedSequence = Math.max(0, Math.trunc(confirmedSequence));
    this.queued.clear();
  }

  confirm(confirmedSequence: number): void {
    this.confirmedSequence = Math.max(
      this.confirmedSequence,
      Math.trunc(confirmedSequence),
    );
    for (const sequence of this.queued.keys()) {
      if (sequence <= this.confirmedSequence) {
        this.queued.delete(sequence);
      }
    }
  }

  accept(batch: KanbanEventBatch): KanbanAccumulatorResult {
    if (batch.board_id !== this.boardId) {
      return this.required('board');
    }
    if (batch.requested_after_sequence !== this.confirmedSequence) {
      return this.required('cursor');
    }
    if (
      batch.snapshot_required ||
      batch.gap_detected ||
      batch.overflow ||
      batch.auth_renewal?.required
    ) {
      return this.required('server');
    }

    for (const event of batch.events) {
      if (event.board_id !== this.boardId) {
        return this.required('board');
      }
      if (event.sequence <= this.confirmedSequence) {
        continue;
      }
      const duplicate = this.queued.get(event.sequence);
      if (duplicate && duplicate.event_id !== event.event_id) {
        return this.required('duplicate');
      }
      if (duplicate) {
        continue;
      }
      if (this.queued.size >= this.maxQueuedEvents) {
        return this.required('overflow');
      }
      this.queued.set(event.sequence, event);
    }

    const ordered = [...this.queued.values()].sort(
      (left, right) => left.sequence - right.sequence,
    );
    let expected = this.confirmedSequence + 1;
    const contiguous: KanbanLiveEvent[] = [];
    for (const event of ordered) {
      if (event.sequence !== expected) {
        return this.required('gap');
      }
      contiguous.push(event);
      expected += 1;
    }
    if (batch.latest_sequence > this.confirmedSequence && contiguous.length === 0) {
      return this.required('gap');
    }
    return { events: contiguous, snapshotRequired: false };
  }

  private required(
    reason: NonNullable<KanbanAccumulatorResult['reason']>,
  ): KanbanAccumulatorResult {
    this.queued.clear();
    return { events: [], snapshotRequired: true, reason };
  }
}

@Injectable({ providedIn: 'root' })
export class KanbanLiveApiClient implements KanbanLiveApiPort {
  private readonly core = inject(HubApiCoreService);
  private readonly compatibilityApi = inject(KanbanApiClient);

  snapshot(baseUrl: string, boardId: string): Observable<KanbanSnapshot> {
    return this.core
      .get<KanbanSnapshot | { data: KanbanSnapshot }>(
        `${this.root(baseUrl)}/api/v1/kanban/boards/${encodeURIComponent(boardId)}/snapshot`,
        baseUrl,
        undefined,
        false,
      )
      .pipe(
        map((response) => this.unwrapData(response)),
        catchError((error: unknown) => {
          if (!(error instanceof HttpErrorResponse) || error.status !== 404) {
            return throwError(() => error);
          }
          return forkJoin({
            board: this.compatibilityApi.board(baseUrl, boardId),
            cards: this.compatibilityApi.cards(baseUrl, boardId, {}),
          }).pipe(
            map(({ board, cards }) => ({
              schema_version: 'kanban.snapshot.v1' as const,
              board,
              cards: cards.items,
              event_sequence: 0,
            })),
          );
        }),
        map(normalizeKanbanSnapshot),
      );
  }

  events(
    baseUrl: string,
    boardId: string,
    afterSequence: number,
    limit = 128,
  ): Observable<KanbanEventBatch> {
    const cursor = Math.max(0, Math.trunc(afterSequence));
    const url =
      `${this.root(baseUrl)}/api/v1/kanban/boards/${encodeURIComponent(boardId)}/events` +
      `?after_sequence=${cursor}&limit=${Math.max(1, Math.min(200, Math.trunc(limit)))}`;
    return this.core
      .request<KanbanEventBatch | { data: KanbanEventBatch }>(
        'GET',
        url,
        baseUrl,
        { headers: { 'Last-Event-ID': String(cursor) } },
      )
      .pipe(map((response) => this.unwrapData(response)));
  }

  private unwrapData<T>(response: T | { data: T }): T {
    if (
      response &&
      typeof response === 'object' &&
      'data' in response
    ) {
      return (response as { data: T }).data;
    }
    return response as T;
  }

  private root(baseUrl: string): string {
    return baseUrl.replace(/\/+$/, '').replace(/\/api\/v1\/kanban$/, '');
  }
}

export interface KanbanLiveSessionConfig {
  pollMs: number;
  retryInitialMs: number;
  retryMaxMs: number;
  maxQueuedEvents: number;
}

const DEFAULT_SESSION_CONFIG: KanbanLiveSessionConfig = {
  pollMs: 1_000,
  retryInitialMs: 250,
  retryMaxMs: 5_000,
  maxQueuedEvents: 128,
};

export class KanbanLiveSession {
  constructor(
    private readonly api: KanbanLiveApiPort,
    private readonly auth: KanbanLiveAuthPort,
    private readonly config: KanbanLiveSessionConfig = DEFAULT_SESSION_CONFIG,
  ) {}

  connect(baseUrl: string, boardId: string): Observable<KanbanLiveUpdate> {
    return new Observable<KanbanLiveUpdate>((observer) => {
      let active = true;
      let generation = 0;
      let currentToken: string | null | undefined;
      let hadAuthenticatedToken = false;
      let hasSnapshot = false;
      let confirmedSequence = 0;
      let retryDelay = this.config.retryInitialMs;
      let request: Subscription | undefined;
      let refresh: Subscription | undefined;
      let requestSerial = 0;
      let refreshSerial = 0;
      let retryTimer: ReturnType<typeof setTimeout> | undefined;
      let refreshAttemptedForToken: string | null | undefined;
      let failedClosedToken: string | null | undefined;
      const accumulator = new KanbanEventAccumulator(
        boardId,
        this.config.maxQueuedEvents,
      );

      const cancelIo = (): void => {
        requestSerial += 1;
        refreshSerial += 1;
        request?.unsubscribe();
        request = undefined;
        refresh?.unsubscribe();
        refresh = undefined;
        if (retryTimer !== undefined) {
          clearTimeout(retryTimer);
          retryTimer = undefined;
        }
      };

      const subscribeRequest = (
        source: Observable<unknown>,
        handlers: {
          next: (value: unknown) => void;
          error: (error: unknown) => void;
        },
      ): void => {
        const serial = ++requestSerial;
        request?.unsubscribe();
        request = undefined;
        const candidate = source.subscribe(handlers);
        if (serial === requestSerial && !candidate.closed) {
          request = candidate;
        } else if (serial !== requestSerial) {
          candidate.unsubscribe();
        }
      };

      const schedule = (delay: number, operation: () => void): void => {
        if (!active) {
          return;
        }
        if (retryTimer !== undefined) {
          clearTimeout(retryTimer);
        }
        retryTimer = setTimeout(() => {
          retryTimer = undefined;
          operation();
        }, Math.max(0, delay));
      };

      const failClosed = (
        status: 'authentication' | 'forbidden',
        message: string,
      ): void => {
        failedClosedToken = currentToken ?? null;
        cancelIo();
        observer.next({ kind: 'error', status, message });
      };

      const restartForCurrentToken = (reloadSnapshot: boolean): void => {
        generation += 1;
        const nextGeneration = generation;
        cancelIo();
        retryDelay = this.config.retryInitialMs;
        if (reloadSnapshot) {
          hasSnapshot = false;
          confirmedSequence = 0;
          accumulator.reset(0);
        }
        if (hasSnapshot) {
          poll(nextGeneration);
        } else {
          loadSnapshot(nextGeneration);
        }
      };

      const renewAuthentication = (
        requestGeneration: number,
        source: 'snapshot' | 'events',
      ): void => {
        if (!active || requestGeneration !== generation) {
          return;
        }
        const token = currentToken ?? null;
        if (refreshAttemptedForToken === token) {
          failClosed(
            'authentication',
            'Kanban-Live-Verbindung konnte nicht authentifiziert werden.',
          );
          return;
        }
        refreshAttemptedForToken = token;
        requestSerial += 1;
        request?.unsubscribe();
        request = undefined;
        const serial = ++refreshSerial;
        const candidate = this.auth.refreshToken().subscribe({
          next: ({ access_token }) => {
            if (!active || requestGeneration !== generation) {
              return;
            }
            if (access_token === currentToken) {
              restartForCurrentToken(source === 'snapshot');
            }
          },
          error: () => {
            if (active && requestGeneration === generation) {
              failClosed(
                'authentication',
                'Kanban-Anmeldung konnte nicht erneuert werden.',
              );
            }
          },
        });
        if (serial === refreshSerial && !candidate.closed) {
          refresh = candidate;
        } else if (serial !== refreshSerial) {
          candidate.unsubscribe();
        }
      };

      const handleError = (
        error: unknown,
        requestGeneration: number,
        retry: () => void,
        source: 'snapshot' | 'events',
      ): void => {
        if (!active || requestGeneration !== generation) {
          return;
        }
        const status =
          error instanceof HttpErrorResponse
            ? error.status
            : (error as { status?: number } | null)?.status;
        if (status === 403) {
          failClosed('forbidden', 'Keine Berechtigung fuer Live-Kanban-Daten.');
          return;
        }
        if (status === 401) {
          renewAuthentication(requestGeneration, source);
          return;
        }
        if (source === 'events' && status === 404) {
          return;
        }
        if (source === 'snapshot') {
          observer.next({
            kind: 'error',
            status: 'snapshot',
            message: 'Kanban-Snapshot konnte nicht geladen werden.',
          });
        }
        const delay = retryDelay;
        retryDelay = Math.min(
          this.config.retryMaxMs,
          Math.max(this.config.retryInitialMs, retryDelay * 2),
        );
        schedule(delay, retry);
      };

      const loadSnapshot = (requestGeneration: number): void => {
        if (!active || requestGeneration !== generation) {
          return;
        }
        subscribeRequest(this.api.snapshot(baseUrl, boardId), {
          next: (value) => {
            if (!active || requestGeneration !== generation) {
              return;
            }
            const rawSnapshot = value as KanbanSnapshot;
            const snapshot = normalizeKanbanSnapshot(rawSnapshot);
            if (
              snapshot.board.id !== boardId ||
              !Number.isFinite(snapshot.event_sequence)
            ) {
              handleError(
                { status: 0 },
                requestGeneration,
                () => loadSnapshot(requestGeneration),
                'snapshot',
              );
              return;
            }
            confirmedSequence = snapshot.event_sequence;
            accumulator.reset(confirmedSequence);
            hasSnapshot = true;
            retryDelay = this.config.retryInitialMs;
            observer.next({ kind: 'snapshot', snapshot });
            schedule(this.config.pollMs, () => poll(requestGeneration));
          },
          error: (error) =>
            handleError(
              error,
              requestGeneration,
              () => loadSnapshot(requestGeneration),
              'snapshot',
            ),
        });
      };

      const poll = (requestGeneration: number): void => {
        if (!active || requestGeneration !== generation || !hasSnapshot) {
          return;
        }
        subscribeRequest(
          this.api.events(
            baseUrl,
            boardId,
            confirmedSequence,
            this.config.maxQueuedEvents,
          ),
          {
            next: (value) => {
              if (!active || requestGeneration !== generation) {
                return;
              }
              const batch = value as KanbanEventBatch;
              retryDelay = this.config.retryInitialMs;
              if (batch.auth_renewal?.required) {
                renewAuthentication(requestGeneration, 'events');
                return;
              }
              const accepted = accumulator.accept(batch);
              if (accepted.snapshotRequired || accepted.events.length > 0) {
                loadSnapshot(requestGeneration);
                return;
              }
              schedule(batch.has_more ? 0 : this.config.pollMs, () =>
                poll(requestGeneration),
              );
            },
            error: (error) =>
              handleError(
                error,
                requestGeneration,
                () => poll(requestGeneration),
                'events',
              ),
          },
        );
      };

      const tokenSubscription = this.auth.token$.subscribe((token) => {
        if (!active || token === currentToken) {
          return;
        }
        const previousToken = currentToken;
        currentToken = token;
        if (token) {
          hadAuthenticatedToken = true;
        }
        if (previousToken !== undefined && previousToken && !token) {
          generation += 1;
          cancelIo();
          hasSnapshot = false;
          confirmedSequence = 0;
          accumulator.reset(0);
          observer.next({
            kind: 'error',
            status: 'signed-out',
            message: 'Kanban-Live-Verbindung wurde nach der Abmeldung beendet.',
          });
          return;
        }
        if (!token && hadAuthenticatedToken) {
          return;
        }
        if (failedClosedToken !== undefined && failedClosedToken === token) {
          return;
        }
        failedClosedToken = undefined;
        restartForCurrentToken(!hasSnapshot);
      });

      return () => {
        active = false;
        generation += 1;
        cancelIo();
        tokenSubscription.unsubscribe();
      };
    });
  }
}

export const KANBAN_LIVE_SESSION_CONFIG =
  new InjectionToken<KanbanLiveSessionConfig>('KANBAN_LIVE_SESSION_CONFIG', {
    providedIn: 'root',
    factory: () => DEFAULT_SESSION_CONFIG,
  });

@Injectable({ providedIn: 'root' })
export class KanbanLiveBoardService {
  private readonly api = inject(KanbanLiveApiClient);
  private readonly auth = inject(UserAuthService);
  private readonly config = inject(KANBAN_LIVE_SESSION_CONFIG);

  connect(baseUrl: string, boardId: string): Observable<KanbanLiveUpdate> {
    return new KanbanLiveSession(this.api, this.auth, this.config).connect(
      baseUrl,
      boardId,
    );
  }
}
