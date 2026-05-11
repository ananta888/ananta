import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubRepairApiClient {
  private core = inject(HubApiCoreService);

  listCandidates(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/repair/candidates`, body, baseUrl, token);
  }

  listRuntimeTargets(baseUrl: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/repair/runtime-targets`, baseUrl, token);
  }

  analyze(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/repair/analyze`, body, baseUrl, token);
  }

  preview(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/repair/preview`, body, baseUrl, token);
  }

  execute(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/repair/execute`, body, baseUrl, token);
  }

  getOutcome(baseUrl: string, planId: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/repair/outcome/${planId}`, baseUrl, token);
  }
}
