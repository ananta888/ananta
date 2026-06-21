import { Injectable, inject } from '@angular/core';
import { Observable, of, throwError, combineLatest } from 'rxjs';
import { catchError, debounceTime, distinctUntilChanged, map, shareReplay, startWith, switchMap } from 'rxjs/operators';
import { BehaviorSubject } from 'rxjs';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { CodeCompassService } from './code-compass.service';
import {
  ChServiceError,
  ChSearchResult,
  ChSearchRequest,
  ChExplanationReadModel,
} from '../models/codehug.models';

/**
 * SearchService — CH-007: Volltext + Symbol-Suche + Erklaermodus.
 *
 * SOLID: SRP — Suche orchestriert, Erklaerungen ueber LLM-Backend.
 * Wichtig: Erklaerungen gehen durch deterministische Phase zuerst
 * (heuristic rules), nur bei Nicht-Treffer kommt LLM.
 *
 * Performance: in-memory Cache fuer haeufige Queries (TTL 60s).
 */
@Injectable({ providedIn: 'root' })
export class SearchService {
  private readonly hub = inject(HubApiCoreService);
  private readonly dir = inject(AgentDirectoryService);
  private readonly compass = inject(CodeCompassService);

  /** Eingabe-Stream (gebaut in der Komponente). */
  private readonly query$ = new BehaviorSubject<string>('');
  private readonly mode$ = new BehaviorSubject<ChSearchRequest['mode']>('hybrid');
  private readonly debounced$ = this.query$.pipe(
    debounceTime(180),
    distinctUntilChanged(),
  );

  /** In-Memory Cache (TTL 60s). */
  private cache = new Map<string, { ts: number; value: ChSearchResult[] }>();
  private readonly cacheTtlMs = 60_000;

  // Public streams for components
  readonly queryStream = this.query$.asObservable();
  readonly modeStream = this.mode$.asObservable();

  private hubUrl(): string {
    const h = this.dir.list().find(a => a.role === 'hub');
    if (!h) throw new ChServiceError('not_found', 'Kein Hub-Agent registriert');
    return h.url;
  }

  setQuery(q: string): void {
    this.query$.next(q);
  }

  setMode(m: ChSearchRequest['mode']): void {
    this.mode$.next(m);
  }

  /**
   * Live-Search: reagiert auf query$/mode$.
   * Kombinator: bei nicht-leerem Query live, sonst leeres Array.
   */
  liveResults(): Observable<ChSearchResult[]> {
    return combineLatest([this.debounced$, this.mode$]).pipe(
      switchMap(([q, mode]) => {
        if (!q || q.trim().length < 2) return of([] as ChSearchResult[]);
        return this.search({ query: q, mode }).pipe(
          catchError(() => of([] as ChSearchResult[])),
        );
      }),
      startWith([] as ChSearchResult[]),
      shareReplay({ bufferSize: 1, refCount: true }),
    );
  }

  /**
   * Direkter Search (single call).
   */
  search(req: ChSearchRequest): Observable<ChSearchResult[]> {
    const key = `${req.mode}::${req.query}::${req.workspacePath ?? ''}`;
    const cached = this.cache.get(key);
    if (cached && Date.now() - cached.ts < this.cacheTtlMs) {
      return of(cached.value);
    }
    const url = `${this.hubUrl()}/api/search`;
    return this.hub.post<ChSearchResult[]>(url, req, this.hubUrl()).pipe(
      map(r => r ?? []),
      map(results => {
        this.cache.set(key, { ts: Date.now(), value: results });
        return results;
      }),
      catchError(err => throwError(() => this.toChError(err, 'search'))),
    );
  }

  /**
   * Erklaermodus: kurze deterministische Erklaerung, fallback LLM.
   * Liefert:
   * - heuristische Erklaerung (Zusammenfassung aus Signatur, JSDoc, Callers)
   * - verwandte Symbole
   * - LLM-Detailerklaerung (wenn heuristisch unzureichend)
   */
  explain(symbolId: string): Observable<ChExplanationReadModel> {
    return this.compass.getSymbolDetail(symbolId).pipe(
      switchMap(detail => {
        // Heuristik
        const heur = this.heuristicExplanation(detail);
        // Fallback zu LLM nur wenn documentation UND callees BEIDE leer
        // (d.h. das Hub hat keine substanziellen Daten — Symbol ist praktisch unbekannt)
        const hasSubstantialData = (detail?.documentation && detail.documentation.length > 30)
          || (Array.isArray(detail?.callees) && detail.callees.length > 0);
        if (!hasSubstantialData) {
          return this.requestLlmExplanation(symbolId, heur);
        }
        return of(heur);
      }),
      catchError(err => throwError(() => this.toChError(err, 'explain'))),
    );
  }

  /** Cache-Invalidation. */
  invalidateCache(): void {
    this.cache.clear();
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Helpers
  // ─────────────────────────────────────────────────────────────────────────

  private heuristicExplanation(detail: any): ChExplanationReadModel {
    const sig = detail?.signature ?? '';
    const doc = detail?.documentation ?? '';
    const callers = Array.isArray(detail?.callers) ? detail.callers.length : 0;
    const callees = Array.isArray(detail?.callees) ? detail.callees.length : 0;
    const summary = doc || `Funktion ${detail?.name ?? ''} mit Signatur "${sig}". ${callers} Aufrufer, ${callees} Aufrufe.`;
    return {
      symbolId: detail?.id ?? '',
      kind: 'heuristic',
      summary,
      details: [
        `Signatur: ${sig}`,
        `Datei: ${detail?.filePath ?? ''}`,
        `Aufrufer: ${callers}`,
        `Aufrufe: ${callees}`,
      ],
      relatedSymbols: (detail?.callees ?? []).slice(0, 5).map((c: any) => c.id).filter(Boolean),
      llmEnhanced: false,
      generatedAt: Date.now(),
    };
  }

  private requestLlmExplanation(symbolId: string, base: ChExplanationReadModel): Observable<ChExplanationReadModel> {
    const url = `${this.hubUrl()}/api/llm/explain`;
    return this.hub.post<{ explanation: string; related?: string[] }>(url, { symbolId }, this.hubUrl()).pipe(
      map(resp => ({
        symbolId,
        kind: 'llm' as const,
        summary: resp.explanation || base.summary,
        details: base.details,
        relatedSymbols: resp.related ?? base.relatedSymbols,
        llmEnhanced: true,
        generatedAt: Date.now(),
      })),
    );
  }

  private toChError(err: unknown, op: string): ChServiceError {
    let code: any = 'unknown';
    let message = `${op} failed`;
    if (err instanceof Error) message = `${op}: ${err.message}`;
    if (typeof err === 'object' && err !== null) {
      const status = (err as any).status;
      if (status === 401) code = 'unauthorized';
      else if (status === 404) code = 'not_found';
      else if (status === 422) code = 'validation_error';
      else if (status === 0) code = 'network_error';
      else if (typeof status === 'number' && status >= 500) code = 'backend_error';
    }
    return new ChServiceError(code, message, err);
  }
}