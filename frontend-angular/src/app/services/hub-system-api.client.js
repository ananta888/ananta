var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { HubApiCoreService } from './hub-api-core.service';
let HubSystemApiClient = class HubSystemApiClient {
    constructor() {
        this.core = inject(HubApiCoreService);
    }
    listAgents(baseUrl, token) { return this.core.get(`${baseUrl}/api/system/agents`, baseUrl, token, false); }
    getStats(baseUrl, token) { return this.core.get(`${baseUrl}/api/system/stats`, baseUrl, token, false); }
    getStatsHistory(baseUrl, token) { return this.core.get(`${baseUrl}/api/system/stats/history`, baseUrl, token, false); }
    getAuditLogs(baseUrl, limit = 100, offset = 0, token) { return this.core.get(`${baseUrl}/api/system/audit-logs?limit=${limit}&offset=${offset}`, baseUrl, token, false); }
    analyzeAuditLogs(baseUrl, limit = 50, token) { return this.core.post(`${baseUrl}/api/system/audit/analyze?limit=${limit}`, {}, baseUrl, token, false, 60000); }
    streamSystemEvents(baseUrl, token) { return this.core.streamSystemEvents(baseUrl, token); }
};
HubSystemApiClient = __decorate([
    Injectable({ providedIn: 'root' })
], HubSystemApiClient);
export { HubSystemApiClient };
//# sourceMappingURL=hub-system-api.client.js.map