import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, throwError } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubConfigApiClient {
  private core = inject(HubApiCoreService);
  getConfig(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/config`, baseUrl, token, true); }
  setConfig(baseUrl: string, cfg: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/config`, cfg, baseUrl, token); }
  getAssistantReadModel(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/assistant/read-model`, baseUrl, token, true); }
  getDashboardReadModel(
    baseUrl: string,
    optionsOrToken?: { benchmarkTaskKind?: string; ttlMs?: number } | string,
    tokenOrTtlMs?: string | number,
    legacyTtlMs?: number,
  ): Observable<any> {
    const options = typeof optionsOrToken === 'string' || optionsOrToken == null ? undefined : optionsOrToken;
    const token = typeof optionsOrToken === 'string' ? optionsOrToken : typeof tokenOrTtlMs === 'string' ? tokenOrTtlMs : undefined;
    const ttlMs = typeof tokenOrTtlMs === 'number' ? tokenOrTtlMs : options?.ttlMs ?? legacyTtlMs ?? 4000;
    const benchmarkTaskKind = (options?.benchmarkTaskKind || 'analysis').trim() || 'analysis';
    const cacheKey = `dashboard-read-model:${benchmarkTaskKind}`;
    const q = new URLSearchParams();
    q.set('benchmark_task_kind', benchmarkTaskKind);
    const url = `${baseUrl}/dashboard/read-model?${q.toString()}`;
    const cached = this.core.cacheGet(baseUrl, cacheKey, ttlMs);
    if (cached) return new Observable((observer) => { observer.next(cached); observer.complete(); });
    return this.core.get<any>(url, baseUrl, token, true).pipe(
      map((data) => {
        this.core.cacheSet(baseUrl, cacheKey, data);
        return data;
      }),
      catchError((err) => {
        const stale = this.core.cacheGet(baseUrl, cacheKey, 24 * 60 * 60 * 1000);
        if (stale) return new Observable((observer) => { observer.next(stale); observer.complete(); });
        return throwError(() => err);
      }),
    );
  }
  listProviders(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/providers`, baseUrl, token, true); }
  listProviderCatalog(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/providers/catalog`, baseUrl, token, true); }
  getLlmBenchmarks(baseUrl: string, filters?: { task_kind?: string; top_n?: number }, token?: string): Observable<any> {
    const q = new URLSearchParams();
    if (filters?.task_kind) q.set('task_kind', filters.task_kind);
    if (filters?.top_n) q.set('top_n', String(filters.top_n));
    const query = q.toString();
    return this.core.get<any>(`${baseUrl}/llm/benchmarks${query ? `?${query}` : ''}`, baseUrl, token, true);
  }
  getLlmBenchmarksConfig(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/llm/benchmarks/config`, baseUrl, token, true); }
}
