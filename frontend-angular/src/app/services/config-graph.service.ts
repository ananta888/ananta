import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  ApplyPatchResult,
  ConfigGraph,
  EffectiveConfig,
  PatchOp,
  ValidationResult,
} from '../models/config-graph.model';

@Injectable({ providedIn: 'root' })
export class ConfigGraphService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = '/api/config-graph';

  getGraph(): Observable<ConfigGraph> {
    return this.http.get<ConfigGraph>(this.baseUrl);
  }

  getEffectiveConfig(payload: {
    surface: string;
    task_kind?: string | null;
    path?: string | null;
  }): Observable<EffectiveConfig> {
    return this.http.post<EffectiveConfig>(`${this.baseUrl}/effective`, payload);
  }

  validatePatch(ops: PatchOp[]): Observable<ValidationResult> {
    return this.http.post<ValidationResult>(`${this.baseUrl}/validate-patch`, { ops });
  }

  applyPatch(ops: PatchOp[], approvalToken?: string): Observable<ApplyPatchResult> {
    return this.http.post<ApplyPatchResult>(`${this.baseUrl}/apply-patch`, {
      ops,
      approval_token: approvalToken ?? '',
    });
  }
}
