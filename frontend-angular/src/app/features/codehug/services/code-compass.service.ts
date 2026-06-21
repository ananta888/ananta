import { Injectable, inject } from '@angular/core';
import { Observable, of, throwError } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
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
 * - HubApiCoreService (Auth, Timeout, Retry, Unwrap)
 * - AgentDirectoryService (Hub-URL-Aufloesung)
 *
 * Komponenten nutzen ausschliesslich diesen Service, niemals HttpClient direkt.
 */
@Injectable({ providedIn: 'root' })
export class CodeCompassService {
  private readonly hub = inject(HubApiCoreService);
  private readonly dir = inject(AgentDirectoryService);

  /** Liefert die URL des konfigurierten Hub. */
  private hubUrl(): string {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      throw new ChServiceError('not_found', 'Kein Hub-Agent im AgentDirectory registriert.');
    }
    return hub.url;
  }

  /**
   * Listet alle Dateien fuer ein Projekt.
   */
  listFiles(projectId: string): Observable<ChFileReadModel[]> {
    const url = `${this.hubUrl()}/api/codecompass/projects/${encodeURIComponent(projectId)}/files`;
    return this.hub.get<{ files: any[] } | ChFileReadModel[]>(url, this.hubUrl()).pipe(
      map(resp => {
        const arr = Array.isArray(resp) ? resp : (resp.files ?? []);
        return arr.map(f => this.normalizeFile(f));
      }),
      catchError(err => throwError(() => this.toChError(err, 'listFiles'))),
    );
  }

  /**
   * Listet alle bekannten Projekte.
   */
  listProjects(): Observable<ChProjectReadModel[]> {
    const url = `${this.hubUrl()}/api/codecompass/projects`;
    return this.hub.get<{ projects: any[] } | ChProjectReadModel[]>(url, this.hubUrl()).pipe(
      map(resp => {
        const arr = Array.isArray(resp) ? resp : (resp.projects ?? []);
        return arr.map(p => this.normalizeProject(p));
      }),
      catchError(err => throwError(() => this.toChError(err, 'listProjects'))),
    );
  }

  /**
   * Liest Projekt-Metadaten fuer ein gegebenes Projekt.
   */
  getProject(projectId: string): Observable<ChProjectReadModel> {
    const url = `${this.hubUrl()}/api/codecompass/projects/${encodeURIComponent(projectId)}`;
    return this.hub.get<ChProjectReadModel>(url, this.hubUrl()).pipe(
      map(raw => this.normalizeProject(raw)),
      catchError(err => throwError(() => this.toChError(err, 'getProject'))),
    );
  }

  /**
   * Loest Kontext-Vorschlaege zu einer Aufgabe auf.
   * Backend-Endpoint: /api/codecompass/reload-context
   */
  resolveContext(request: ChResolveContextRequest): Observable<ChResolveContextResponse> {
    const url = `${this.hubUrl()}/api/codecompass/reload-context`;
    return this.hub.post<ChResolveContextResponse>(
      url,
      { task_id: request.projectId, request: { description: request.taskDescription, max_suggestions: request.maxSuggestions } },
      this.hubUrl(),
    ).pipe(
      map(resp => this.normalizeResolveContext(resp)),
      catchError(err => throwError(() => this.toChError(err, 'resolveContext'))),
    );
  }

  /**
   * Semantische Symbolsuche.
   * Backend-Endpoint: /api/codecompass/query (type=symbol_search)
   */
  searchSymbols(request: ChSearchSymbolsRequest): Observable<ChSearchSymbolsResponse> {
    const params = new URLSearchParams({
      type: 'symbol_search',
      seed: request.query,
    });
    if (request.kinds && request.kinds.length > 0) {
      params.set('kinds', request.kinds.join(','));
    }
    if (request.limit) {
      params.set('limit', String(request.limit));
    }
    const url = `${this.hubUrl()}/api/codecompass/query?${params.toString()}`;
    return this.hub.get<ChSearchSymbolsResponse>(url, this.hubUrl()).pipe(
      map(resp => this.normalizeSearchSymbols(resp)),
      catchError(err => throwError(() => this.toChError(err, 'searchSymbols'))),
    );
  }

  /**
   * Liefert Kontext zu einer Datei (deterministische Fakten + KI-Summary).
   */
  getFileContext(request: ChGetFileContextRequest): Observable<ChGetFileContextResponse> {
    const params = new URLSearchParams({
      type: 'file_context',
      seed: request.filePath,
    });
    if (request.includeSymbols) {
      params.set('include_symbols', '1');
    }
    const url = `${this.hubUrl()}/api/codecompass/query?${params.toString()}`;
    return this.hub.get<ChGetFileContextResponse>(url, this.hubUrl()).pipe(
      map(resp => this.normalizeFileContext(resp)),
      catchError(err => throwError(() => this.toChError(err, 'getFileContext'))),
    );
  }

  /**
   * Liefert Detail zu einem Symbol (Signatur, Doku, Caller, Callee).
   * Backend-Endpoint: /api/codecompass/query (type=symbol_detail)
   */
  getSymbolDetail(symbolId: string): Observable<ChSymbolDetailReadModel> {
    const params = new URLSearchParams({
      type: 'symbol_detail',
      seed: symbolId,
    });
    const url = `${this.hubUrl()}/api/codecompass/query?${params.toString()}`;
    return this.hub.get<ChSymbolDetailReadModel>(url, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'getSymbolDetail'))),
    );
  }

  /**
   * Plant Kontext-Gruppen fuer eine Aufgabe.
   * Backend-Endpoint: /api/codecompass/query (type=plan_context)
   */
  planContext(request: ChPlanContextRequest): Observable<ChPlanContextResponse> {
    const url = `${this.hubUrl()}/api/codecompass/query`;
    return this.hub.post<ChPlanContextResponse>(
      url,
      { type: 'plan_context', description: request.taskDescription, strategy: request.strategy ?? 'anchored' },
      this.hubUrl(),
    ).pipe(
      map(resp => this.normalizePlanContext(resp)),
      catchError(err => throwError(() => this.toChError(err, 'planContext'))),
    );
  }

  /**
   * Stösst eine Re-Indexierung des Projekts an.
   */
  triggerReindex(projectId: string): Observable<{ jobId: string }> {
    const url = `${this.hubUrl()}/api/codecompass/reindex`;
    return this.hub.post<{ job_id: string }>(url, { project_id: projectId }, this.hubUrl()).pipe(
      map(resp => ({ jobId: resp.job_id })),
      catchError(err => throwError(() => this.toChError(err, 'triggerReindex'))),
    );
  }

  /** Health-check: liefert true wenn CodeCompass antwortet. */
  healthCheck(): Observable<boolean> {
    const url = `${this.hubUrl()}/api/codecompass/health`;
    return this.hub.get<{ status: string }>(url, this.hubUrl(), undefined, false).pipe(
      map(resp => resp.status === 'ok'),
      catchError(() => of(false)),
    );
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