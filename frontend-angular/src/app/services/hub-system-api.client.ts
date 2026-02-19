import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubSystemApiClient {
  private core = inject(HubApiCoreService);
  listAgents(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/api/system/agents`, baseUrl, token, false); }
  getStats(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/api/system/stats`, baseUrl, token, false); }
  getStatsHistory(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/api/system/stats/history`, baseUrl, token, false); }
  getAuditLogs(baseUrl: string, limit = 100, offset = 0, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/api/system/audit-logs?limit=${limit}&offset=${offset}`, baseUrl, token, false); }
  analyzeAuditLogs(baseUrl: string, limit = 50, token?: string): Observable<any> { return this.core.post(`${baseUrl}/api/system/audit/analyze?limit=${limit}`, {}, baseUrl, token, false, 60000); }
  streamSystemEvents(baseUrl: string, token?: string): Observable<any> { return this.core.streamSystemEvents(baseUrl, token); }
}
