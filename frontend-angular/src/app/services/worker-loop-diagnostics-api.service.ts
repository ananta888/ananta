import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiBaseService } from './api-base.service';

// AWTCL-019 / AWWPI-019: tool loop and workspace mutation diagnostics.
@Injectable({ providedIn: 'root' })
export class WorkerLoopDiagnosticsApiService extends ApiBaseService {
  listRuns(baseUrl: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/api/diagnostics/ananta-worker/runs`, baseUrl, token);
  }

  getReport(baseUrl: string, workspace: string, kind: string, token?: string): Observable<any> {
    const params = `workspace=${encodeURIComponent(workspace)}&kind=${encodeURIComponent(kind)}`;
    return this.core.get<any>(`${baseUrl}/api/diagnostics/ananta-worker/report?${params}`, baseUrl, token);
  }
}
