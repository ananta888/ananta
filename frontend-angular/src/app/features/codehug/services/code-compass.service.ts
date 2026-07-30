import { Injectable, inject } from '@angular/core';
import { SourceControlV1ApiClient } from '../../../services/source-control-v1-api.client';
import { Observable, of, throwError } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

import { SourceControlProjection } from '../../../models/source-control-v1-api.model';
import {
  ChProjectReadModel,
  ChFileReadModel,
  ChResolveContextRequest,
  ChResolveContextResponse,
  ChSearchSymbolsRequest,
  ChSearchSymbolsResponse,
  ChGetFileContextRequest,
  ChGetFileContextResponse,
  ChPlanContextRequest,
  ChPlanContextResponse,
  ChServiceError,
  ChServiceErrorCode,
  ChSymbolDetailReadModel,
} from '../models/codehug.models';

/**
 * CodeCompassService — kapselt alle Aufrufe an die Hub-/CodeCompass-API
 * fuer das CodeHug-Feature.
 *
 * SOLID: SRP — dieser Service ist ausschliesslich fuer CodeCompass-Reads
 * zustaendig. Schreibende Operationen (Re-Index, Context-Pakete speichern)
 * liegen in anderen Services.
 *
 * Abhaengigkeiten:
 * - SourceControlV1ApiClient (kanonische, Hub-autorisierte Read-API)
 *
 * Komponenten nutzen ausschliesslich diesen Service, niemals HttpClient direkt.
 */
@Injectable({ providedIn: 'root' })
export class CodeCompassService {
  private readonly sourceControlApi = inject(SourceControlV1ApiClient);

  /**
   * Listet alle Dateien fuer ein Projekt.
   */
  listFiles(projectId: string): Observable<ChFileReadModel[]> {
    return throwError(() => new ChServiceError(
      'not_found',
      `listFiles: Für KnowledgeIndex ${projectId} existiert kein Hub-Dateilisten-Endpunkt.`,
    ));
  }

  /**
   * Listet alle bekannten Projekte.
   */
  listProjects(): Observable<ChProjectReadModel[]> {
    return this.sourceControlApi.listConnections({ limit: 200 }).pipe(
      map(page => page.items
        .filter(item => item.active_index !== null)
        .map(item => this.projectionAsProject(item))),
      catchError(err => throwError(() => this.toChError(err, 'listProjects'))),
    );
  }

  /**
   * Liest Projekt-Metadaten fuer ein gegebenes Projekt.
   */
  getProject(projectId: string): Observable<ChProjectReadModel> {
    return this.listProjects().pipe(
      map(projects => {
        const project = projects.find(item => item.id === projectId);
        if (!project) {
          throw new ChServiceError('not_found', `KnowledgeIndex ${projectId} wurde nicht gefunden.`);
        }
        return project;
      }),
      catchError(err => throwError(() => this.toChError(err, 'getProject'))),
    );
  }

  /**
   * Loest Kontext-Vorschlaege zu einer Aufgabe auf.
   * Kanonische Query ist an die servergelieferte Connection-ID gebunden.
   */
  resolveContext(request: ChResolveContextRequest): Observable<ChResolveContextResponse> {
    return this.sourceControlApi.queryConnection(request.projectId, {
      query: request.taskDescription,
      limit: request.maxSuggestions,
    }).pipe(
      map(resp => this.normalizeResolveContext(resp['payload'] ?? resp)),
      catchError(err => throwError(() => this.toChError(err, 'resolveContext'))),
    );
  }

  /**
   * Semantische Symbolsuche.
   * Semantische Query über die kanonische Connection-Route.
   */
  searchSymbols(request: ChSearchSymbolsRequest): Observable<ChSearchSymbolsResponse> {
    return this.sourceControlApi.queryConnection(request.projectId, {
      query: request.query,
    }).pipe(
      map(resp => this.normalizeSearchSymbols(resp['payload'] ?? resp)),
      catchError(err => throwError(() => this.toChError(err, 'searchSymbols'))),
    );
  }

  /**
   * Liefert Kontext zu einer Datei (deterministische Fakten + KI-Summary).
   */
  getFileContext(request: ChGetFileContextRequest): Observable<ChGetFileContextResponse> {
    return this.sourceControlApi.queryConnection(request.projectId, {
      query: request.filePath,
    }).pipe(
      map(resp => this.normalizeFileContext(resp['payload'] ?? resp)),
      catchError(err => throwError(() => this.toChError(err, 'getFileContext'))),
    );
  }

  /**
   * Liefert Detail zu einem Symbol (Signatur, Doku, Caller, Callee).
   * Detailquery über die kanonische Connection-Route.
   */
  getSymbolDetail(symbolId: string, knowledgeIndexId?: string): Observable<ChSymbolDetailReadModel> {
    if (!knowledgeIndexId) {
      return throwError(() => new ChServiceError(
        'validation_error',
        'getSymbolDetail: knowledge_index_id ist erforderlich.',
      ));
    }
    return this.sourceControlApi.queryConnection(knowledgeIndexId, {
      query: symbolId,
    }).pipe(
      map(resp => (resp['payload'] ?? resp) as unknown as ChSymbolDetailReadModel),
      catchError(err => throwError(() => this.toChError(err, 'getSymbolDetail'))),
    );
  }

  /**
   * Plant Kontext-Gruppen fuer eine Aufgabe.
   * Planungskontext über die kanonische Connection-Route.
   */
  planContext(request: ChPlanContextRequest): Observable<ChPlanContextResponse> {
    return this.sourceControlApi.queryConnection(request.projectId, {
      query: request.taskDescription,
    }).pipe(
      map(resp => this.normalizePlanContext(resp['payload'] ?? resp)),
      catchError(err => throwError(() => this.toChError(err, 'planContext'))),
    );
  }

  /** Readiness wird über die vorhandene Index-Route abgeleitet. */
  healthCheck(): Observable<boolean> {
    return this.sourceControlApi.listConnections({ limit: 1 }).pipe(
      map(() => true),
      catchError(() => of(false)),
    );
  }

  lifecycleCapabilities() {
    return [];
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Normalisierung der Backend-Antworten in CodeHug-Models
  // ─────────────────────────────────────────────────────────────────────────

  private normalizeProject(raw: any): ChProjectReadModel {
    return {
      id: raw.id ?? '',
      name: raw.name ?? '',
      rootPath: raw.root_path ?? raw.rootPath ?? '',
      languageBreakdown: raw.language_breakdown ?? raw.languageBreakdown ?? {},
      frameworkSignals: raw.framework_signals ?? raw.frameworkSignals ?? [],
      moduleCount: raw.module_count ?? raw.moduleCount ?? 0,
      fileCount: raw.file_count ?? raw.fileCount ?? 0,
      symbolCount: raw.symbol_count ?? raw.symbolCount ?? 0,
      lastIndexedAt: raw.last_indexed_at ?? raw.lastIndexedAt ?? null,
      indexStatus: raw.index_status ?? raw.indexStatus ?? 'missing',
    };
  }

  private projectionAsProject(index: SourceControlProjection): ChProjectReadModel {
    const connection = index.connection;
    const activeIndex = index.active_index ?? {};
    const indexMetadata = index.index ?? {};
    return this.normalizeProject({
      id: index.connection_id,
      name: String(connection['display_name'] || index.connection_id),
      root_path: '',
      language_breakdown: indexMetadata['language_breakdown'] || {},
      framework_signals: indexMetadata['framework_signals'] || [],
      module_count: indexMetadata['module_count'] || 0,
      file_count: indexMetadata['file_count'] || 0,
      symbol_count: indexMetadata['symbol_count'] || 0,
      last_indexed_at: activeIndex['updated_at'] || null,
      index_status: String(indexMetadata['status'] || 'missing'),
    });
  }

  private normalizeResolveContext(raw: any): ChResolveContextResponse {
    return {
      suggestions: (raw?.suggestions ?? []).map((s: any) => ({
        symbolId: s.symbol_id ?? undefined,
        filePath: s.file_path ?? undefined,
        reason: s.reason ?? '',
        relevanceScore: typeof s.relevance === 'number' ? s.relevance : 0,
        source: s.source ?? 'resolve_context',
      })),
      resolvedSymbols: (raw?.resolved_symbols ?? raw?.symbols ?? []).map((s: any) => this.normalizeSymbol(s)),
      estimatedTokenCount: raw?.estimated_token_count ?? 0,
    };
  }

  private normalizeSearchSymbols(raw: any): ChSearchSymbolsResponse {
    return {
      symbols: (raw?.symbols ?? []).map((s: any) => this.normalizeSymbol(s)),
      totalMatches: raw?.total_matches ?? raw?.total ?? (raw?.symbols?.length ?? 0),
    };
  }

  private normalizeFileContext(raw: any): ChGetFileContextResponse {
    return {
      file: this.normalizeFile(raw?.file ?? {}),
      symbols: (raw?.symbols ?? []).map((s: any) => this.normalizeSymbol(s)),
      deterministicFacts: (raw?.deterministic_facts ?? []).map((f: any) => ({
        key: f.key,
        value: f.value,
        source: f.source ?? 'parser',
      })),
      llmSummary: raw?.llm_summary ?? null,
      llmSummaryConfidence: typeof raw?.llm_summary_confidence === 'number' ? raw.llm_summary_confidence : null,
    };
  }

  private normalizePlanContext(raw: any): ChPlanContextResponse {
    return {
      groups: (raw?.groups ?? []).map((g: any) => ({
        name: g.name ?? 'unnamed',
        description: g.description ?? '',
        filePaths: g.file_paths ?? [],
        symbolIds: g.symbol_ids ?? [],
        reasoning: g.reasoning ?? '',
        estimatedTokens: g.estimated_tokens ?? 0,
      })),
      warnings: raw?.warnings ?? [],
      estimatedTokenCount: raw?.estimated_token_count ?? 0,
    };
  }

  private normalizeSymbol(s: any): any {
    return {
      id: s.id ?? s.symbol_id ?? '',
      name: s.name ?? '',
      qualifiedName: s.qualified_name ?? s.qualifiedName ?? s.name ?? '',
      kind: s.kind ?? 'function',
      filePath: s.file_path ?? s.filePath ?? '',
      lineStart: s.line_start ?? s.lineStart ?? 0,
      lineEnd: s.line_end ?? s.lineEnd ?? 0,
      signature: s.signature,
      visibility: s.visibility ?? 'unknown',
      docSummary: s.doc_summary ?? s.docSummary,
    };
  }

  private normalizeFile(f: any): any {
    return {
      path: f.path ?? '',
      language: f.language ?? 'unknown',
      sizeBytes: f.size_bytes ?? f.sizeBytes ?? 0,
      lastModified: f.last_modified ?? f.lastModified ?? 0,
      symbolIds: f.symbol_ids ?? f.symbolIds ?? [],
      isSensitive: f.is_sensitive ?? f.isSensitive ?? false,
    };
  }

  private toChError(err: unknown, operation: string): ChServiceError {
    let code: ChServiceErrorCode = 'unknown';
    let message = `${operation} failed`;
    if (err instanceof Error) {
      message = `${operation}: ${err.message}`;
      if (err.name === 'TimeoutError') code = 'timeout';
    }
    if (typeof err === 'object' && err !== null) {
      const status = (err as any).status;
      if (status === 401) code = 'unauthorized';
      else if (status === 403) code = 'forbidden';
      else if (status === 404) code = 'not_found';
      else if (status === 422) code = 'validation_error';
      else if (status === 0 || status === undefined) code = 'network_error';
      else if (typeof status === 'number' && status >= 500) code = 'backend_error';
    }
    return new ChServiceError(code, message, err);
  }
}
