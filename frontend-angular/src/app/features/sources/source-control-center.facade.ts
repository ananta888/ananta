import { Injectable, computed, inject, signal } from '@angular/core';
import { finalize } from 'rxjs';

import {
  SourceControlApiError,
  SourceControlViewState,
  toSourceControlApiError,
} from '../../models/source-control-contracts';
import {
  SourceControlNextAction,
  SourceControlProjection,
} from '../../models/source-control-v1-api.model';
import { SourceControlV1ApiClient } from '../../services/source-control-v1-api.client';

export interface SourceOverviewSource {
  readonly source_id: string;
  readonly display_name: string;
  readonly source_type: string;
  readonly enabled: boolean;
  readonly latest_snapshot: { readonly status: string } | null;
}

export interface SourceOverviewIndex {
  readonly knowledge_index_id: string;
  readonly status: string;
  readonly profile_name: string | null;
}

export interface SourceOverviewRow {
  readonly source: SourceOverviewSource;
  readonly activeIndex: SourceOverviewIndex | null;
  readonly etag: string;
  readonly nextActions: readonly SourceControlNextAction[];
}

@Injectable()
export class SourceControlCenterFacade {
  private readonly sourceControlApi = inject(SourceControlV1ApiClient);

  readonly rows = signal<readonly SourceOverviewRow[]>([]);
  readonly nextCursor = signal<string | null>(null);
  readonly loading = signal(false);
  readonly loaded = signal(false);
  readonly mutating = signal(false);
  readonly error = signal<SourceControlApiError | null>(null);

  readonly viewState = computed<SourceControlViewState>(() => {
    if (this.loading() && !this.loaded()) return 'loading';
    if (this.error()) return this.error()!.kind;
    if (this.loaded() && this.rows().length === 0) return 'empty';
    return 'ready';
  });

  readonly stateMessage = computed(() => {
    const messages: Record<SourceControlViewState, string> = {
      loading: 'Quellen und Indexstände werden geladen.',
      empty: 'Noch keine Quellen vorhanden.',
      ready: '',
      offline: 'Der Hub ist offline oder nicht konfiguriert.',
      unauthorized: 'Die Anmeldung ist abgelaufen. Bitte erneut anmelden.',
      forbidden: 'Sie dürfen die Quellen dieses Bereichs nicht anzeigen.',
      'not-found': 'Der Source-Control-Bereich ist auf diesem Hub nicht verfügbar.',
      conflict: 'Der Quellenstand wurde parallel geändert. Bitte neu laden.',
      unprocessable: 'Der Hub lieferte einen nicht unterstützten Source-Vertrag.',
      'rate-limited': 'Zu viele Anfragen. Bitte warten und erneut laden.',
      'server-error': 'Der Hub konnte die Quellen derzeit nicht laden.',
      error: 'Die Quellen konnten nicht geladen werden.',
    };
    return messages[this.viewState()];
  });

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.sourceControlApi.listConnections({ limit: 200 }).pipe(
      finalize(() => this.loading.set(false)),
    ).subscribe({
      next: page => {
        this.rows.set(page.items.map(item => this.projectionRow(item)));
        this.nextCursor.set(page.next_cursor);
        this.loaded.set(true);
      },
      error: error => this.captureError(error),
    });
  }

  loadMore(): void {
    const cursor = this.nextCursor();
    if (!cursor || this.loading()) return;
    this.loading.set(true);
    this.error.set(null);
    this.sourceControlApi.listConnections({
      cursor,
      limit: 200,
    }).pipe(
      finalize(() => this.loading.set(false)),
    ).subscribe({
      next: page => {
        const existing = new Map(
          this.rows().map(row => [row.source.source_id, row]),
        );
        for (const projection of page.items) {
          const row = this.projectionRow(projection);
          existing.set(row.source.source_id, row);
        }
        this.rows.set([...existing.values()]);
        this.nextCursor.set(page.next_cursor);
      },
      error: error => this.captureError(error),
    });
  }

  refreshSource(sourceId: string): void {
    const row = this.rows().find(item => item.source.source_id === sourceId);
    if (
      !row ||
      !row.nextActions.includes('refresh') ||
      this.mutating()
    ) return;
    this.mutating.set(true);
    this.error.set(null);
    this.sourceControlApi.refreshConnection(sourceId, {
      etag: row.etag,
      idempotencyKey: this.idempotencyKey(),
    }).pipe(
      finalize(() => this.mutating.set(false)),
    ).subscribe({
      next: () => this.load(),
      error: error => this.captureError(error),
    });
  }

  scanSource(sourceId: string): void {
    const row = this.rows().find(item => item.source.source_id === sourceId);
    if (!row || !row.nextActions.includes('scan') || this.mutating()) return;
    this.mutating.set(true);
    this.error.set(null);
    this.sourceControlApi.scanConnection(sourceId, {
      etag: row.etag,
      idempotencyKey: this.idempotencyKey(),
    }).pipe(
      finalize(() => this.mutating.set(false)),
    ).subscribe({
      next: () => this.load(),
      error: error => this.captureError(error),
    });
  }

  private projectionRow(
    projection: SourceControlProjection,
  ): SourceOverviewRow {
    const connection = projection.connection;
    const revision = projection.revision;
    const admission = projection.admission;
    const index = projection.active_index ?? projection.index;
    const knowledgeIndexId =
      typeof index?.['knowledge_index_id'] === 'string'
        ? index['knowledge_index_id']
        : '';
    const state =
      typeof connection['state'] === 'string'
        ? connection['state']
        : '';
    return {
      source: {
        source_id: projection.connection_id,
        display_name:
          typeof connection['display_name'] === 'string'
            ? connection['display_name']
            : projection.connection_id,
        source_type:
          typeof connection['connector_type'] === 'string'
            ? connection['connector_type']
            : 'unbekannt',
        enabled: state !== 'disabled' && state !== 'tombstoned',
        latest_snapshot: revision === null ? null : {
          status:
            typeof admission?.['state'] === 'string'
              ? admission['state']
              : 'unbekannt',
        },
      },
      activeIndex: knowledgeIndexId ? {
        knowledge_index_id: knowledgeIndexId,
        status:
          typeof index?.['status'] === 'string'
            ? index['status']
            : 'active',
        profile_name:
          typeof index?.['profile_name'] === 'string'
            ? index['profile_name']
            : null,
      } : null,
      etag: projection.etag,
      nextActions: projection.next_actions,
    };
  }

  private captureError(error: unknown): void {
    this.error.set(
      error instanceof SourceControlApiError
        ? error
        : toSourceControlApiError(error, 'source_control_center_load'),
    );
  }

  private idempotencyKey(): string {
    return `ui:${crypto.randomUUID()}`;
  }
}
