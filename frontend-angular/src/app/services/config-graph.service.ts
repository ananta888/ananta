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
import { AgentDirectoryService } from './agent-directory.service';

@Injectable({ providedIn: 'root' })
export class ConfigGraphService {
  private readonly http = inject(HttpClient);
  private readonly dir = inject(AgentDirectoryService);

  private get baseUrl(): string {
    const hub = this.dir.list().find(a => a.role === 'hub');
    const origin = hub?.url ?? 'http://127.0.0.1:5000';
    return `${origin}/api/config-graph`;
  }

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

  createConfigEntry(
    entryType: 'agent_profile' | 'path_rule',
    data: Record<string, unknown>,
  ): Observable<ConfigGraph> {
    return this.http.post<ConfigGraph>(`${this.baseUrl}/create-config-entry`, { entry_type: entryType, data });
  }
}
