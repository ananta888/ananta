import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiBaseService } from './api-base.service';

// ALWA-010: persistent approval lifecycle API (digest-bound requests).
@Injectable({ providedIn: 'root' })
export class ApprovalsApiService extends ApiBaseService {
  listRequests(baseUrl: string, status?: string, token?: string): Observable<any> {
    const query = status ? `?status=${encodeURIComponent(status)}` : '';
    return this.core.get<any>(`${baseUrl}/api/approvals${query}`, baseUrl, token);
  }

  decide(baseUrl: string, requestId: string, decision: 'granted' | 'denied', reason?: string, token?: string): Observable<any> {
    return this.core.post<any>(
      `${baseUrl}/api/approvals/${encodeURIComponent(requestId)}/decision`,
      { decision, reason: reason || undefined },
      baseUrl,
      token,
    );
  }
}
