import { DestroyRef, Injectable, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, Subscription, debounceTime, distinctUntilChanged, forkJoin } from 'rxjs';

import { NotificationService } from '../../../services/notification.service';
import { SystemFacade } from '../../system/system.facade';
import {
  KanbanActivity,
  KanbanApiClient,
  KanbanBoard,
  KanbanBoardSummary,
  KanbanCard,
  KanbanColumnId,
  KanbanComment,
  KanbanFilters,
} from './kanban-api.client';
import { KanbanLiveBoardService } from './kanban-live-events';

function requestId(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `kanban-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function isRevisionConflict(error: unknown): boolean {
  return error instanceof HttpErrorResponse
    ? error.status === 409
    : !!error && typeof error === 'object' && Number((error as { status?: unknown }).status) === 409;
}

export function optimisticMove(
  cards: readonly KanbanCard[],
  cardId: string,
  columnId: KanbanColumnId,
  targetPosition: number,
): readonly KanbanCard[] {
  const moved = cards.find(card => card.id === cardId);
  if (!moved) return cards;
  const target = cards
    .filter(card => card.id !== cardId && card.column_id === columnId)
    .sort((left, right) => left.position - right.position);
  target.splice(Math.min(Math.max(targetPosition, 0), target.length), 0, {
    ...moved,
    column_id: columnId,
    status: columnId,
  });
  const ranked = new Map(target.map((card, index) => [card.id, { ...card, position: index }]));
  return cards.map(card => ranked.get(card.id) ?? card);
}

@Injectable()
export class KanbanStore {
  private readonly api = inject(KanbanApiClient);
  private readonly system = inject(SystemFacade);
  private readonly notifications = inject(NotificationService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly live = inject(KanbanLiveBoardService);
  private readonly searchChanges = new Subject<string>();
  private baseUrl = '';
  private boardListRequest?: Subscription;
  private viewRequest?: Subscription;
  private viewGeneration = 0;
  private liveRequest?: Subscription;
  private liveGeneration = 0;

  readonly boards = signal<readonly KanbanBoardSummary[]>([]);
  readonly board = signal<KanbanBoard | null>(null);
  readonly cards = signal<readonly KanbanCard[]>([]);
  readonly filters = signal<KanbanFilters>({});
  readonly selectedCard = signal<KanbanCard | null>(null);
  readonly comments = signal<readonly KanbanComment[]>([]);
  readonly activity = signal<readonly KanbanActivity[]>([]);
  readonly loading = signal(false);
  readonly detailLoading = signal(false);
  readonly error = signal('');

  constructor() {
    this.destroyRef.onDestroy(() => this.stopLive());
    this.searchChanges.pipe(
      debounceTime(300),
      distinctUntilChanged(),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(value => {
      this.filters.update(current => ({ ...current, q: value.trim() || undefined }));
      this.loadCards();
    });
  }

  load(): void {
    this.stopLive();
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) {
      this.error.set('Kein Hub-Agent konfiguriert.');
      return;
    }
    const generation = ++this.viewGeneration;
    this.boardListRequest?.unsubscribe();
    this.viewRequest?.unsubscribe();
    this.baseUrl = hub.url;
    this.loading.set(true);
    this.error.set('');
    this.boardListRequest = this.api.boards(this.baseUrl)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: page => {
        if (generation !== this.viewGeneration) return;
        this.boards.set(page.items);
        const boardId = this.board()?.id && page.items.some(item => item.id === this.board()?.id)
          ? this.board()!.id
          : page.items[0]?.id;
        if (!boardId) {
          this.loading.set(false);
          this.error.set('Kein zugreifbares Board vorhanden.');
          return;
        }
        this.selectBoard(boardId);
      },
      error: () => {
        if (generation !== this.viewGeneration) return;
        this.loading.set(false);
        this.error.set('Boards konnten nicht geladen werden.');
      },
    });
  }

  selectBoard(boardId: string): void {
    if (!this.baseUrl || !boardId) return;
    ++this.viewGeneration;
    this.boardListRequest?.unsubscribe();
    this.viewRequest?.unsubscribe();
    this.stopLive();
    const generation = this.liveGeneration;
    this.loading.set(true);
    this.error.set('');
    this.closeDetail();
    this.liveRequest = this.live.connect(this.baseUrl, boardId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: update => {
          if (generation !== this.liveGeneration) return;
          if (update.kind === 'snapshot') {
            this.board.set(update.snapshot.board);
            this.cards.set(update.snapshot.cards);
            this.loading.set(false);
            this.error.set('');
            return;
          }
          this.loading.set(false);
          this.error.set(update.message);
        },
        error: () => {
          if (generation !== this.liveGeneration) return;
          this.loading.set(false);
          this.error.set('Board-Live-Verbindung ist fehlgeschlagen.');
        },
      });
  }

  private stopLive(): void {
    this.liveGeneration += 1;
    this.liveRequest?.unsubscribe();
    this.liveRequest = undefined;
  }

  reloadSnapshot(): void {
    const boardId = this.board()?.id;
    if (boardId) this.selectBoard(boardId);
    else this.load();
  }

  loadCards(): void {
    const boardId = this.board()?.id;
    if (!this.baseUrl || !boardId) return;
    const generation = ++this.viewGeneration;
    this.viewRequest?.unsubscribe();
    this.loading.set(true);
    this.viewRequest = this.api.cards(this.baseUrl, boardId, this.filters())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: page => {
        if (generation !== this.viewGeneration) return;
        this.cards.set(page.items);
        this.loading.set(false);
        this.error.set('');
      },
      error: () => {
        if (generation !== this.viewGeneration) return;
        this.loading.set(false);
        this.error.set('Gefilterte Karten konnten nicht geladen werden.');
      },
    });
  }

  search(value: string): void {
    this.searchChanges.next(value);
  }

  setFilter(filter: Partial<KanbanFilters>): void {
    this.filters.update(current => ({ ...current, ...filter }));
    this.loadCards();
  }

  cardsFor(columnId: KanbanColumnId): readonly KanbanCard[] {
    return this.cards()
      .filter(card => card.column_id === columnId)
      .sort((left, right) => left.position - right.position);
  }

  create(title: string, description = ''): void {
    const boardId = this.board()?.id;
    if (!this.baseUrl || !boardId || !title.trim()) return;
    this.api.createCard(this.baseUrl, boardId, {
      title: title.trim(),
      description: description.trim() || undefined,
      idempotency_key: requestId(),
    }).subscribe({
      next: () => {
        this.notifications.success('Karte angelegt');
        this.reloadSnapshot();
      },
      error: () => this.error.set('Karte konnte nicht angelegt werden.'),
    });
  }

  move(card: KanbanCard, columnId: KanbanColumnId, position: number): void {
    const boardId = this.board()?.id;
    if (!this.baseUrl || !boardId) return;
    this.cards.set(optimisticMove(this.cards(), card.id, columnId, position));
    this.api.moveCard(this.baseUrl, card.id, {
      board_id: boardId,
      expected_revision: card.revision,
      idempotency_key: requestId(),
      column_id: columnId,
      position,
    }).subscribe({
      next: updated => {
        this.cards.update(cards => cards.map(item => item.id === updated.id ? updated : item));
        this.notifications.success(`Karte nach ${columnId} verschoben`);
      },
      error: error => {
        this.notifications.error(
          isRevisionConflict(error)
            ? 'Board wurde parallel geändert. Snapshot wird neu geladen.'
            : 'Verschieben fehlgeschlagen. Snapshot wird wiederhergestellt.',
        );
        this.reloadSnapshot();
      },
    });
  }

  openDetail(card: KanbanCard): void {
    const boardId = this.board()?.id;
    if (!this.baseUrl || !boardId) return;
    this.selectedCard.set(card);
    this.detailLoading.set(true);
    forkJoin({
      card: this.api.card(this.baseUrl, boardId, card.id),
      comments: this.api.comments(this.baseUrl, boardId, card.id),
      activity: this.api.activity(this.baseUrl, boardId, card.id),
    }).subscribe({
      next: value => {
        this.selectedCard.set(value.card);
        this.comments.set(value.comments.items);
        this.activity.set(value.activity.items);
        this.detailLoading.set(false);
      },
      error: () => {
        this.detailLoading.set(false);
        this.error.set('Kartendetails konnten nicht geladen werden.');
      },
    });
  }

  closeDetail(): void {
    this.selectedCard.set(null);
    this.comments.set([]);
    this.activity.set([]);
  }

  addComment(body: string): void {
    const card = this.selectedCard();
    const boardId = this.board()?.id;
    if (!this.baseUrl || !boardId || !card || !body.trim()) return;
    this.api.comment(this.baseUrl, card.id, {
      board_id: boardId,
      expected_revision: card.revision,
      idempotency_key: requestId(),
      body: body.trim(),
    }).subscribe({
      next: updated => {
        this.selectedCard.set(updated);
        this.openDetail(updated);
      },
      error: error => {
        if (isRevisionConflict(error)) this.reloadSnapshot();
        this.error.set('Kommentar konnte nicht gespeichert werden.');
      },
    });
  }

  updateDependencies(raw: string): void {
    const card = this.selectedCard();
    const boardId = this.board()?.id;
    if (!this.baseUrl || !boardId || !card) return;
    const dependencies = [...new Set(raw.split(',').map(value => value.trim()).filter(Boolean))];
    this.api.setDependencies(this.baseUrl, card.id, {
      board_id: boardId,
      expected_revision: card.revision,
      idempotency_key: requestId(),
      dependencies,
    }).subscribe({
      next: updated => {
        this.selectedCard.set(updated);
        this.cards.update(cards => cards.map(item => item.id === updated.id ? updated : item));
      },
      error: error => {
        if (isRevisionConflict(error)) this.reloadSnapshot();
        this.error.set('Abhängigkeiten konnten nicht gespeichert werden.');
      },
    });
  }
}
