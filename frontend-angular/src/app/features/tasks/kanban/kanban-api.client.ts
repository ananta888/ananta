import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { HubApiCoreService } from '../../../services/hub-api-core.service';

export type KanbanColumnId = 'todo' | 'in_progress' | 'blocked' | 'completed';
export type KanbanCapability =
  | 'kanban.read'
  | 'kanban.write'
  | 'kanban.assign'
  | 'kanban.comment'
  | 'kanban.admin';

export interface KanbanColumn {
  readonly id: KanbanColumnId;
  readonly title: string;
  readonly statuses: readonly string[];
  readonly card_count: number;
}

export interface KanbanAssignee {
  readonly id: string;
  readonly name: string | null;
  readonly url: string | null;
}

export interface KanbanCard {
  readonly schema_version: 'kanban.v1';
  readonly id: string;
  readonly board_id: string;
  readonly title: string;
  readonly description: string | null;
  readonly status: string;
  readonly column_id: KanbanColumnId;
  readonly position: number;
  readonly revision: number;
  readonly priority: string;
  readonly assignee: KanbanAssignee | null;
  readonly labels: readonly string[];
  readonly blocked: boolean;
  readonly dependencies: readonly string[];
  readonly comment_count: number;
  readonly activity_count: number;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface KanbanBoardSummary {
  readonly id: string;
  readonly name: string;
  readonly scope_type: 'hub' | 'goal' | 'team';
  readonly scope_id: string | null;
  readonly revision: string;
  readonly card_count: number;
  readonly capabilities: readonly KanbanCapability[];
}

export interface KanbanBoard extends KanbanBoardSummary {
  readonly columns: readonly KanbanColumn[];
}

export interface KanbanCardPage {
  readonly board_id: string;
  readonly board_revision: string;
  readonly items: readonly KanbanCard[];
  readonly next_cursor: string | null;
}

export interface KanbanComment {
  readonly id: string;
  readonly card_id: string;
  readonly author_id: string;
  readonly body: string;
  readonly created_at: string;
}

export interface KanbanActivity {
  readonly id: string;
  readonly card_id: string;
  readonly event_type: string;
  readonly actor_id: string | null;
  readonly message: string;
  readonly details: Readonly<Record<string, unknown>>;
  readonly created_at: string;
}

export interface KanbanFilters {
  readonly q?: string;
  readonly column_id?: KanbanColumnId;
  readonly assignee_id?: string;
  readonly blocked?: boolean;
}

function unwrap<T>(value: unknown): T {
  const body = value && typeof value === 'object' && 'data' in value
    ? (value as { data: unknown }).data
    : value;
  return body as T;
}

function query(filters: KanbanFilters): string {
  const params = new URLSearchParams({ limit: '100' });
  if (filters.q) params.set('q', filters.q);
  if (filters.column_id) params.set('column_id', filters.column_id);
  if (filters.assignee_id) params.set('assignee_id', filters.assignee_id);
  if (filters.blocked !== undefined) params.set('blocked', String(filters.blocked));
  return params.toString();
}

@Injectable({ providedIn: 'root' })
export class KanbanApiClient {
  private readonly api = inject(HubApiCoreService);

  private root(baseUrl: string): string {
    return `${baseUrl.replace(/\/$/, '')}/api/v1/kanban`;
  }

  private get<T>(baseUrl: string, path: string): Observable<T> {
    return this.api.get<unknown>(`${this.root(baseUrl)}${path}`, baseUrl, undefined, false)
      .pipe(map(value => unwrap<T>(value)));
  }

  private post<T>(baseUrl: string, path: string, body: unknown): Observable<T> {
    return this.api.post<unknown>(`${this.root(baseUrl)}${path}`, body, baseUrl)
      .pipe(map(value => unwrap<T>(value)));
  }

  capabilities(baseUrl: string): Observable<{ capabilities: readonly KanbanCapability[] }> {
    return this.get(baseUrl, '/capabilities');
  }

  boards(baseUrl: string): Observable<{ items: readonly KanbanBoardSummary[] }> {
    return this.get(baseUrl, '/boards?limit=100');
  }

  board(baseUrl: string, boardId: string): Observable<KanbanBoard> {
    return this.get(baseUrl, `/boards/${encodeURIComponent(boardId)}`);
  }

  cards(baseUrl: string, boardId: string, filters: KanbanFilters): Observable<KanbanCardPage> {
    return this.get(
      baseUrl,
      `/boards/${encodeURIComponent(boardId)}/cards?${query(filters)}`,
    );
  }

  card(baseUrl: string, boardId: string, cardId: string): Observable<KanbanCard> {
    return this.get(
      baseUrl,
      `/boards/${encodeURIComponent(boardId)}/cards/${encodeURIComponent(cardId)}`,
    );
  }

  comments(baseUrl: string, boardId: string, cardId: string):
    Observable<{ items: readonly KanbanComment[] }> {
    return this.get(
      baseUrl,
      `/boards/${encodeURIComponent(boardId)}/cards/${encodeURIComponent(cardId)}/comments`,
    );
  }

  activity(baseUrl: string, boardId: string, cardId: string):
    Observable<{ items: readonly KanbanActivity[] }> {
    return this.get(
      baseUrl,
      `/boards/${encodeURIComponent(boardId)}/cards/${encodeURIComponent(cardId)}/activity`,
    );
  }

  createCard(
    baseUrl: string,
    boardId: string,
    command: { title: string; description?: string; priority?: string; idempotency_key: string },
  ): Observable<KanbanCard> {
    return this.post(baseUrl, `/boards/${encodeURIComponent(boardId)}/cards`, command);
  }

  moveCard(
    baseUrl: string,
    cardId: string,
    command: {
      board_id: string;
      expected_revision: number;
      idempotency_key: string;
      column_id: KanbanColumnId;
      position: number;
    },
  ): Observable<KanbanCard> {
    return this.post(baseUrl, `/cards/${encodeURIComponent(cardId)}/commands/move`, command);
  }

  comment(
    baseUrl: string,
    cardId: string,
    command: {
      board_id: string;
      expected_revision: number;
      idempotency_key: string;
      body: string;
    },
  ): Observable<KanbanCard> {
    return this.post(baseUrl, `/cards/${encodeURIComponent(cardId)}/commands/comment`, command);
  }

  setDependencies(
    baseUrl: string,
    cardId: string,
    command: {
      board_id: string;
      expected_revision: number;
      idempotency_key: string;
      dependencies: readonly string[];
    },
  ): Observable<KanbanCard> {
    return this.post(
      baseUrl,
      `/cards/${encodeURIComponent(cardId)}/commands/set-dependencies`,
      command,
    );
  }
}

