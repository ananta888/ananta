import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, throwError } from 'rxjs';

import {
  SourceDescriptorV1,
  SourcePackV1,
  SourceSnapshotV1,
  normalizeSnapshotList,
  normalizeSourceList,
  normalizeSourcePackList,
  toSourceControlApiError,
} from '../models/source-control-contracts';
import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';

export type SourceSnapshot = SourceSnapshotV1;
export type SourceItem = SourceDescriptorV1;
export type SourcePackItem = SourcePackV1;

@Injectable({ providedIn: 'root' })
export class SourcesService {
  private readonly core = inject(HubApiCoreService);
  private readonly directory = inject(AgentDirectoryService);

  listSources(): Observable<readonly SourceItem[]> {
    const hubUrl = this.hubUrl();
    return this.core.get<unknown>(`${hubUrl}/sources`, hubUrl, undefined, false).pipe(
      map(normalizeSourceList),
      catchError(error => throwError(() => toSourceControlApiError(error, 'sources_load'))),
    );
  }

  listPacks(): Observable<readonly SourcePackItem[]> {
    const hubUrl = this.hubUrl();
    return this.core.get<unknown>(`${hubUrl}/sources/packs`, hubUrl, undefined, false).pipe(
      map(normalizeSourcePackList),
      catchError(error => throwError(() => toSourceControlApiError(error, 'source_packs_load'))),
    );
  }

  bootstrapPack(sourcePackId: string, dryRun: boolean): Observable<Readonly<Record<string, unknown>>> {
    const hubUrl = this.hubUrl();
    return this.core.post<Readonly<Record<string, unknown>>>(
      `${hubUrl}/sources/packs/${encodeURIComponent(sourcePackId)}/bootstrap`,
      { dry_run: dryRun },
      hubUrl,
    ).pipe(
      catchError(error => throwError(() => toSourceControlApiError(error, 'source_pack_bootstrap'))),
    );
  }

  refresh(sourceId: string): Observable<Readonly<Record<string, unknown>>> {
    const hubUrl = this.hubUrl();
    return this.core.post<Readonly<Record<string, unknown>>>(
      `${hubUrl}/sources/${encodeURIComponent(sourceId)}/refresh`,
      {},
      hubUrl,
    ).pipe(
      catchError(error => throwError(() => toSourceControlApiError(error, 'source_refresh'))),
    );
  }

  citation(sourceId: string): Observable<Readonly<Record<string, unknown>>> {
    const hubUrl = this.hubUrl();
    return this.core.get<Readonly<Record<string, unknown>>>(
      `${hubUrl}/sources/${encodeURIComponent(sourceId)}/citation`,
      hubUrl,
      undefined,
      false,
    ).pipe(
      catchError(error => throwError(() => toSourceControlApiError(error, 'source_citation_load'))),
    );
  }

  snapshots(sourceId: string): Observable<readonly SourceSnapshot[]> {
    const hubUrl = this.hubUrl();
    return this.core.get<unknown>(
      `${hubUrl}/sources/${encodeURIComponent(sourceId)}/snapshots`,
      hubUrl,
      undefined,
      false,
    ).pipe(
      map(normalizeSnapshotList),
      catchError(error => throwError(() => toSourceControlApiError(error, 'source_snapshots_load'))),
    );
  }

  importOpenNotebook(
    payload: Record<string, unknown>,
    collectionName?: string,
  ): Observable<Readonly<Record<string, unknown>>> {
    const hubUrl = this.hubUrl();
    const body = collectionName ? this.withCollection(payload, collectionName) : payload;
    return this.core.post<Readonly<Record<string, unknown>>>(
      `${hubUrl}/sources/import/open-notebook`,
      body,
      hubUrl,
    ).pipe(
      catchError(error => throwError(() => toSourceControlApiError(error, 'source_import'))),
    );
  }

  directTextExport(title: string, content: string, collectionName?: string): Record<string, unknown> {
    const normalized = `${title}\n${content}`;
    let hash = 2166136261;
    for (let index = 0; index < normalized.length; index += 1) {
      hash ^= normalized.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    const suffix = (hash >>> 0).toString(16);
    const notebookId = `direct-text-notebook-${suffix}`;
    return {
      schema: 'open_notebook_export.v1',
      export_version: '1',
      source_system: 'open_notebook',
      notebooks: [{ id: notebookId, name: collectionName || 'Direct text imports' }],
      sources: [{
        id: `direct-text-source-${suffix}`,
        title: title.trim(),
        full_text: content.trim(),
        notebook_ids: [notebookId],
        metadata: { direct_text_import: true },
      }],
    };
  }

  private withCollection(
    payload: Record<string, unknown>,
    collectionName: string,
  ): Record<string, unknown> {
    const copy = structuredClone(payload);
    const notebookId = `ui-collection-${collectionName.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
    const notebooks = Array.isArray(copy['notebooks']) ? copy['notebooks'] : [];
    const sources = Array.isArray(copy['sources']) ? copy['sources'] : [];
    copy['notebooks'] = [...notebooks, { id: notebookId, name: collectionName.trim() }];
    copy['sources'] = sources.map(source => ({
      ...(source && typeof source === 'object' ? source : {}),
      notebook_ids: Array.from(new Set([
        ...(source && typeof source === 'object' && Array.isArray((source as Record<string, unknown>)['notebook_ids'])
          ? (source as Record<string, unknown>)['notebook_ids'] as unknown[]
          : []),
        notebookId,
      ])),
    }));
    return copy;
  }

  private hubUrl(): string {
    const hub = this.directory.list().find(agent => agent.role === 'hub')
      || this.directory.list().find(agent => agent.name === 'hub');
    const url = String(hub?.url || '').replace(/\/+$/, '');
    if (!url) {
      throw toSourceControlApiError({ status: 0 }, 'hub_resolution');
    }
    return url;
  }
}
