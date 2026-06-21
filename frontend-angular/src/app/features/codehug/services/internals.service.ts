import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { map, catchError } from 'rxjs/operators';

import { AgentDirectoryService } from '../../../services/agent-directory.service';

export interface AnantaTemplate {
  id: string;
  name: string;
  description: string;
  category: 'scrum' | 'kanban' | 'opencode' | 'system';
  prompt_template: string;
  is_seed: boolean;
}

export interface VpPreset {
  id: string;
  name: string;
  description: string;
  tags: string[];
}

export interface VpSkillProfile {
  id: string;
  name: string;
  description: string;
  role: string;
  task_kinds: string[];
  capabilities: string[];
  tags: string[];
}

export interface VpStepPosition { x: number; y: number; }
export interface VpArtifactRef { name: string; kind: string; required: boolean; description: string; }
export interface VpStepIo { inputs: VpArtifactRef[]; outputs: VpArtifactRef[]; }
export interface VpLoopPolicy { kind: string; max_iterations: number; condition: string | null; }
export interface VpTransitionCondition { kind: string; expression: string | null; output_name: string | null; loop_policy: VpLoopPolicy | null; }
export interface VpEdge { id: string; source: string; target: string; condition: VpTransitionCondition; label: string | null; metadata: Record<string, unknown>; }
export interface VpStep { id: string; label: string; kind: string; role: string | null; agent_skill_profile_id: string | null; io: VpStepIo; position: VpStepPosition; gate: boolean; policy_hints: string[]; metadata: Record<string, unknown>; }
export interface VpGraph { id: string; name: string; description: string; steps: VpStep[]; edges: VpEdge[]; tags: string[]; metadata: Record<string, unknown>; }

export interface VpDryRunResult {
  dry_run: boolean;
  validation: { valid: boolean; errors: string[]; warnings: string[] };
  policy_summary: Record<string, unknown>;
  blueprint: unknown;
  step_count: number;
  edge_count: number;
}

export interface AnantaWorker {
  url: string;
  name: string;
  role: string;
  status: 'online' | 'offline' | 'degraded';
  worker_roles: string[];
  capabilities: string[];
}

export interface AutopilotStatus {
  running: boolean;
  goal: string;
  team_id: string;
  started_at: number | null;
  tick_count: number;
  dispatched_count: number;
  completed_count: number;
  failed_count: number;
  last_error: string | null;
  effective_security_policy: {
    level: string;
    max_concurrency_cap: number;
    allowed_tool_classes: string[];
  };
  circuit_breakers: {
    open_workers: string[];
    open_count: number;
    failure_streak: Record<string, number>;
  };
}

@Injectable({ providedIn: 'root' })
export class InternalsService {
  private readonly http = inject(HttpClient);
  private readonly dir = inject(AgentDirectoryService);

  private hubUrl(): string {
    const hub = this.dir.list().find(a => a.role === 'hub');
    return hub?.url ?? 'http://127.0.0.1:5000';
  }

  getTemplates(): Observable<AnantaTemplate[]> {
    return this.http.get<any>(`${this.hubUrl()}/templates`).pipe(
      map(resp => {
        const raw: any[] = Array.isArray(resp) ? resp : (resp.data ?? []);
        return raw.map(t => this.normalizeTemplate(t));
      }),
      catchError(() => of([])),
    );
  }

  getWorkers(): Observable<AnantaWorker[]> {
    return this.http.get<any>(`${this.hubUrl()}/api/workers`).pipe(
      map(resp => {
        const raw: any[] = resp?.data?.items ?? resp?.items ?? (Array.isArray(resp) ? resp : []);
        return raw.map(w => ({
          url: w.url ?? '',
          name: w.id ?? w.name ?? 'unknown',
          role: w.role ?? 'worker',
          status: this.mapHealth(w.health ?? w.status),
          worker_roles: Array.isArray(w.worker_roles) ? w.worker_roles : [],
          capabilities: Array.isArray(w.capabilities) ? w.capabilities : [],
        } satisfies AnantaWorker));
      }),
      catchError(() => of([])),
    );
  }

  getVpPresets(): Observable<VpPreset[]> {
    return this.http.get<any[]>(`${this.hubUrl()}/api/visual-process/presets`).pipe(
      map(resp => Array.isArray(resp) ? resp : []),
      catchError(() => of([])),
    );
  }

  getVpPreset(id: string): Observable<VpGraph | null> {
    return this.http.get<VpGraph>(`${this.hubUrl()}/api/visual-process/presets/${encodeURIComponent(id)}`).pipe(
      catchError(() => of(null)),
    );
  }

  getVpSkillProfiles(): Observable<VpSkillProfile[]> {
    return this.http.get<any[]>(`${this.hubUrl()}/api/visual-process/skill-profiles`).pipe(
      map(resp => Array.isArray(resp) ? resp : []),
      catchError(() => of([])),
    );
  }

  runDetStep(subtype: string, command: string, expectedResult: string, timeoutSec = 10): Observable<Record<string, unknown>> {
    return this.http.post<Record<string, unknown>>(`${this.hubUrl()}/api/deterministic/run`, {
      subtype, command, expected_result: expectedResult, timeout: timeoutSec,
    }).pipe(
      catchError(err => of({ success: false, error: err?.message ?? 'network error', stdout: '', stderr: '' })),
    );
  }

  dryRunVpGraph(graph: VpGraph): Observable<VpDryRunResult> {
    return this.http.post<VpDryRunResult>(`${this.hubUrl()}/api/visual-process/dry-run`, { graph }).pipe(
      catchError(err => {
        const body = err?.error;
        return of(body as VpDryRunResult);
      }),
    );
  }

  startVpWorkflow(graph: VpGraph, opts: Record<string, string> = {}): Observable<Record<string, unknown>> {
    return this.http.post<Record<string, unknown>>(`${this.hubUrl()}/api/visual-process/workflow/start`, { graph, ...opts }).pipe(
      catchError(err => of({ status: 'error', detail: err?.message ?? 'unknown' })),
    );
  }

  getVpWorkflowStatus(workflowId: string): Observable<Record<string, unknown>> {
    return this.http.get<Record<string, unknown>>(`${this.hubUrl()}/api/visual-process/workflow/${encodeURIComponent(workflowId)}/status`).pipe(
      catchError(() => of({ status: 'not_found' })),
    );
  }

  getAutopilotStatus(): Observable<AutopilotStatus> {
    return this.http.get<any>(`${this.hubUrl()}/tasks/autopilot/status`).pipe(
      map(resp => resp?.data ?? resp),
      catchError(() => of(this.emptyStatus())),
    );
  }

  private normalizeTemplate(t: any): AnantaTemplate {
    const name: string = t.name ?? '';
    let category: AnantaTemplate['category'] = 'system';
    if (name.toLowerCase().includes('scrum') && !name.toLowerCase().includes('opencode')) category = 'scrum';
    else if (name.toLowerCase().includes('opencode')) category = 'opencode';
    else if (name.toLowerCase().includes('kanban')) category = 'kanban';
    return {
      id: t.id ?? '',
      name,
      description: t.description ?? '',
      category,
      prompt_template: t.prompt_template ?? '',
      is_seed: Boolean(t.is_seed),
    };
  }

  private mapHealth(h: string): AnantaWorker['status'] {
    const l = (h ?? '').toLowerCase();
    if (l === 'online' || l === 'healthy') return 'online';
    if (l === 'degraded') return 'degraded';
    return 'offline';
  }

  private emptyStatus(): AutopilotStatus {
    return {
      running: false, goal: '', team_id: '', started_at: null,
      tick_count: 0, dispatched_count: 0, completed_count: 0, failed_count: 0,
      last_error: null,
      effective_security_policy: { level: 'safe', max_concurrency_cap: 1, allowed_tool_classes: [] },
      circuit_breakers: { open_workers: [], open_count: 0, failure_streak: {} },
    };
  }
}
