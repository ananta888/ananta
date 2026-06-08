import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';

export interface ArtifactRef { name: string; kind: string; required: boolean; description?: string; }
export interface StepIOContract { inputs: ArtifactRef[]; outputs: ArtifactRef[]; }
export interface LoopPolicy { kind: string; max_iterations: number; condition?: string; }
export interface TransitionCondition { kind: string; expression?: string; output_name?: string; loop_policy?: LoopPolicy; }
export interface StepPosition { x: number; y: number; }
export interface VpStep {
  id: string; label: string; kind: string; role?: string;
  agent_skill_profile_id?: string;
  io: StepIOContract; position: StepPosition;
  policy_hints: string[]; gate: boolean;
  run_state?: string; metadata?: Record<string, unknown>;
}
export interface VpEdge {
  id: string; source: string; target: string;
  condition: TransitionCondition; label?: string;
}
export interface VpGraph {
  id: string; name: string; description: string; version: string;
  steps: VpStep[]; edges: VpEdge[]; tags: string[]; metadata?: Record<string, unknown>;
}
export interface ValidationIssue { severity: string; code: string; message: string; step_id?: string; edge_id?: string; }
export interface ValidationResult { valid: boolean; error_count: number; warning_count: number; issues: ValidationIssue[]; }
export interface SkillProfile { id: string; name: string; description: string; role: string; task_kinds: string[]; tags: string[]; }
export interface PresetSummary { id: string; name: string; description: string; tags: string[]; }
export interface DryRunResult { dry_run: boolean; validation: ValidationResult; policy_summary: Record<string, unknown>; blueprint: unknown; step_count: number; edge_count: number; }

@Injectable({ providedIn: 'root' })
export class VisualProcessApiService {
  private http = inject(HttpClient);
  private dir  = inject(AgentDirectoryService);

  private get baseUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  listPresets(): Observable<PresetSummary[]> {
    return this.http.get<PresetSummary[]>(`${this.baseUrl}/api/visual-process/presets`);
  }

  getPreset(id: string): Observable<VpGraph> {
    return this.http.get<VpGraph>(`${this.baseUrl}/api/visual-process/presets/${id}`);
  }

  listSkillProfiles(): Observable<SkillProfile[]> {
    return this.http.get<SkillProfile[]>(`${this.baseUrl}/api/visual-process/skill-profiles`);
  }

  validate(graph: VpGraph): Observable<ValidationResult> {
    return this.http.post<ValidationResult>(`${this.baseUrl}/api/visual-process/validate`, graph);
  }

  dryRun(graph: VpGraph): Observable<DryRunResult> {
    return this.http.post<DryRunResult>(`${this.baseUrl}/api/visual-process/dry-run`, graph);
  }

  mermaid(graph: VpGraph, direction: 'LR' | 'TD' = 'LR'): Observable<{ mermaid: string; tui?: string }> {
    return this.http.post<{ mermaid: string; tui?: string }>(
      `${this.baseUrl}/api/visual-process/mermaid`,
      { ...graph, direction, include_tui: true },
    );
  }

  policySummary(graph: VpGraph): Observable<{ summary: Record<string, unknown>; per_step: Record<string, string[]> }> {
    return this.http.post<any>(`${this.baseUrl}/api/visual-process/policy-summary`, graph);
  }
}
