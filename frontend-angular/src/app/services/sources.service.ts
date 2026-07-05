import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, map } from 'rxjs';

export interface SourceSnapshot {
  snapshot_id: string;
  status: string;
  retrieved_at?: string;
  content_hash?: string;
  extensions?: Record<string, any>;
}

export interface SourceItem {
  source_id: string;
  source_type: string;
  display_name: string;
  trust_level: string;
  enabled: boolean;
  fetch_source?: { url?: string; file_path?: string; refresh_interval?: string };
  citation_source?: { canonical_url?: string; title?: string; license_ref?: string };
  latest_snapshot?: SourceSnapshot & { reason_code?: string; human_message?: string };
  extensions?: Record<string, any>;
}

export interface SourcePackItem {
  source_pack_id: string;
  display_name: string;
  version: string;
  sources: Array<{ source_id: string }>;
}

@Injectable({ providedIn: 'root' })
export class SourcesService {
  constructor(private readonly http: HttpClient) {}

  listSources(): Observable<SourceItem[]> {
    return this.http.get<any>('/sources').pipe(map(payload => Array.isArray(payload?.data) ? payload.data : []));
  }

  listPacks(): Observable<SourcePackItem[]> {
    return this.http.get<any>('/sources/packs').pipe(map(payload => Array.isArray(payload?.data) ? payload.data : []));
  }

  bootstrapPack(sourcePackId: string, dryRun: boolean): Observable<any> {
    return this.http.post<any>(`/sources/packs/${encodeURIComponent(sourcePackId)}/bootstrap`, { dry_run: dryRun })
      .pipe(map(payload => payload?.data || {}));
  }

  refresh(sourceId: string): Observable<any> {
    return this.http.post<any>(`/sources/${encodeURIComponent(sourceId)}/refresh`, {});
  }

  citation(sourceId: string): Observable<any> {
    return this.http.get<any>(`/sources/${encodeURIComponent(sourceId)}/citation`)
      .pipe(map(payload => payload?.data || {}));
  }

  snapshots(sourceId: string): Observable<SourceSnapshot[]> {
    return this.http.get<any>(`/sources/${encodeURIComponent(sourceId)}/snapshots`)
      .pipe(map(payload => Array.isArray(payload?.data) ? payload.data : []));
  }

  importOpenNotebook(payload: Record<string, any>, collectionName?: string): Observable<any> {
    const body = collectionName ? this.withCollection(payload, collectionName) : payload;
    return this.http.post<any>('/sources/import/open-notebook', body).pipe(map(response => response?.data || {}));
  }

  directTextExport(title: string, content: string, collectionName?: string): Record<string, any> {
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

  private withCollection(payload: Record<string, any>, collectionName: string): Record<string, any> {
    const copy = structuredClone(payload);
    const notebookId = `ui-collection-${collectionName.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
    copy.notebooks = [...(copy.notebooks || []), { id: notebookId, name: collectionName.trim() }];
    copy.sources = (copy.sources || []).map((source: any) => ({
      ...source,
      notebook_ids: Array.from(new Set([...(source.notebook_ids || []), notebookId])),
    }));
    return copy;
  }
}
