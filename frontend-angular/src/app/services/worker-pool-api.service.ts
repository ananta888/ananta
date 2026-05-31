import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiBaseService } from './api-base.service';

@Injectable({ providedIn: 'root' })
export class WorkerPoolApiService extends ApiBaseService {
  getStatus(baseUrl: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/api/worker-pool/status`, baseUrl, token);
  }

  getLeases(baseUrl: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/api/worker-pool/leases`, baseUrl, token);
  }

  getQueues(baseUrl: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/api/worker-pool/queues`, baseUrl, token);
  }

  getOllamaModels(baseUrl: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/api/worker-pool/ollama-models`, baseUrl, token);
  }

  cleanupStaleLeases(baseUrl: string, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/api/worker-pool/cleanup-stale-leases`, {}, baseUrl, token);
  }
}
