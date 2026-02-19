import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubConfigApiClient {
  private core = inject(HubApiCoreService);
  getConfig(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/config`, baseUrl, token, true); }
  setConfig(baseUrl: string, cfg: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/config`, cfg, baseUrl, token); }
  getAssistantReadModel(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/assistant/read-model`, baseUrl, token, true); }
  getDashboardReadModel(baseUrl: string, token?: string, ttlMs = 4000): Observable<any> {
    const cached = this.core.cacheGet(baseUrl, 'dashboard-read-model', ttlMs);
    if (cached) return new Observable((observer) => { observer.next(cached); observer.complete(); });
    return this.core.get<any>(`${baseUrl}/dashboard/read-model`, baseUrl, token, true).pipe(map((data) => { this.core.cacheSet(baseUrl, 'dashboard-read-model', data); return data; }));
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
