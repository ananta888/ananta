import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubSystemApiClient {
  private core = inject(HubApiCoreService);
  getHealth(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/health`, baseUrl, token, false, 10000); }
  getContracts(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/api/system/contracts`, baseUrl, token, false); }
  getDemoPreview(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/api/demo/preview`, baseUrl, token, false); }
  listAgents(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/api/system/agents`, baseUrl, token, false); }
  restartTerminalSession(baseUrl: string, forwardParam: string, token?: string): Observable<any> { return this.core.post<any>(`${baseUrl}/api/system/terminal/restart-session`, { forward_param: forwardParam }, baseUrl, token, false, 30000); }
  restartProcess(baseUrl: string, token?: string): Observable<any> { return this.core.post<any>(`${baseUrl}/api/system/restart-process`, {}, baseUrl, token, false, 10000); }
  getStats(baseUrl: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/api/system/stats`, baseUrl, token, false); }
  getStatsHistory(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/api/system/stats/history`, baseUrl, token, false); }
  getAuditLogs(baseUrl: string, limit = 100, offset = 0, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/api/system/audit-logs?limit=${limit}&offset=${offset}`, baseUrl, token, false); }
  analyzeAuditLogs(baseUrl: string, limit = 50, token?: string): Observable<any> { return this.core.post(`${baseUrl}/api/system/audit/analyze?limit=${limit}`, {}, baseUrl, token, false, 60000); }
  streamSystemEvents(baseUrl: string, token?: string): Observable<any> { return this.core.streamSystemEvents(baseUrl, token); }
}
