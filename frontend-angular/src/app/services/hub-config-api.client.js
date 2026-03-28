var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, throwError } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
let HubConfigApiClient = class HubConfigApiClient {
    constructor() {
        this.core = inject(HubApiCoreService);
    }
    getConfig(baseUrl, token) { return this.core.get(`${baseUrl}/config`, baseUrl, token, true); }
    setConfig(baseUrl, cfg, token) { return this.core.post(`${baseUrl}/config`, cfg, baseUrl, token); }
    getAssistantReadModel(baseUrl, token) { return this.core.get(`${baseUrl}/assistant/read-model`, baseUrl, token, true); }
    getDashboardReadModel(baseUrl, optionsOrToken, tokenOrTtlMs, legacyTtlMs) {
        const options = typeof optionsOrToken === 'string' || optionsOrToken == null ? undefined : optionsOrToken;
        const token = typeof optionsOrToken === 'string' ? optionsOrToken : typeof tokenOrTtlMs === 'string' ? tokenOrTtlMs : undefined;
        const ttlMs = typeof tokenOrTtlMs === 'number' ? tokenOrTtlMs : options?.ttlMs ?? legacyTtlMs ?? 4000;
        const benchmarkTaskKind = (options?.benchmarkTaskKind || 'analysis').trim() || 'analysis';
        const cacheKey = `dashboard-read-model:${benchmarkTaskKind}`;
        const q = new URLSearchParams();
        q.set('benchmark_task_kind', benchmarkTaskKind);
        const url = `${baseUrl}/dashboard/read-model?${q.toString()}`;
        const cached = this.core.cacheGet(baseUrl, cacheKey, ttlMs);
        if (cached)
            return new Observable((observer) => { observer.next(cached); observer.complete(); });
        return this.core.get(url, baseUrl, token, true).pipe(map((data) => {
            this.core.cacheSet(baseUrl, cacheKey, data);
            return data;
        }), catchError((err) => {
            const stale = this.core.cacheGet(baseUrl, cacheKey, 24 * 60 * 60 * 1000);
            if (stale)
                return new Observable((observer) => { observer.next(stale); observer.complete(); });
            return throwError(() => err);
        }));
    }
    listProviders(baseUrl, token) { return this.core.get(`${baseUrl}/providers`, baseUrl, token, true); }
    listProviderCatalog(baseUrl, token) { return this.core.get(`${baseUrl}/providers/catalog`, baseUrl, token, true); }
    getLlmBenchmarks(baseUrl, filters, token) {
        const q = new URLSearchParams();
        if (filters?.task_kind)
            q.set('task_kind', filters.task_kind);
        if (filters?.top_n)
            q.set('top_n', String(filters.top_n));
        const query = q.toString();
        return this.core.get(`${baseUrl}/llm/benchmarks${query ? `?${query}` : ''}`, baseUrl, token, true);
    }
    getLlmBenchmarksConfig(baseUrl, token) { return this.core.get(`${baseUrl}/llm/benchmarks/config`, baseUrl, token, true); }
};
HubConfigApiClient = __decorate([
    Injectable({ providedIn: 'root' })
], HubConfigApiClient);
export { HubConfigApiClient };
//# sourceMappingURL=hub-config-api.client.js.map