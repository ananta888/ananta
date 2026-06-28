import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';

export interface ArtifactRef { name: string; kind: string; required: boolean; description?: string; }
export interface StepIOContract { inputs: ArtifactRef[]; outputs: ArtifactRef[]; }
export interface LoopPolicy { kind: string; max_iterations: number; condition?: string; break_on_output?: string; }
export interface TransitionCondition { kind: string; expression?: string; output_name?: string; loop_policy?: LoopPolicy; }
export interface StepPosition { x: number; y: number; }
export interface ModelRoutingConfig {
  strategy?: string;
  model_role?: string;
  preferred_profile_id?: string;
  fallback_group_id?: string;
  required_capabilities?: string[];
  requires_json?: boolean;
  requires_tools?: boolean;
  tool_calling_mode?: 'native_tools' | 'prompt_json' | 'both' | 'none';
  allow_cloud?: boolean;
  max_estimated_cost?: number;
  max_estimated_cost_per_run?: number;
  default_model_role?: string;
  require_approval_on_cloud_escalation?: boolean;
  require_approval_above_estimated_cost?: number;
}
export interface ModelProfileSummary {
  profile_id: string;
  provider_id: string;
  model: string;
  model_role: string;
  local: boolean;
  cloud: boolean;
  supports_json: boolean;
  supports_tools: boolean;
  tool_calling_mode?: string;
  cost_class?: string;
  quality_class?: string;
  fallback_group?: string;
  fallback_rank?: number;
  api_key_configured?: boolean;
}
export interface FallbackGroupSummary {
  ordered_profiles: string[];
  max_total_retries?: number;
  stop_on_policy_block?: boolean;
  stop_on_success?: boolean;
}
export interface ModelRoutingProfilesResult {
  profiles: ModelProfileSummary[];
  fallback_groups: Record<string, FallbackGroupSummary>;
  status: string;
}
export interface PerStepModelPlan {
  step_id: string;
  model_role?: string;
  selected_profile_id?: string;
  provider_id?: string;
  model?: string;
  resolver_source?: string;
  resolver_rank?: number;
  fallback_group_id?: string;
  candidate_chain?: string[];
  cloud_allowed?: boolean;
  blocked_candidates?: Record<string, unknown>[];
  policy_decisions?: Record<string, unknown>[];
  estimated_cost?: Record<string, unknown>;
}
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
export interface ValidationIssue { severity: string; code: string; message: string; step_id?: string; edge_id?: string; artifact_name?: string; }
export interface ValidationResult { valid: boolean; error_count: number; warning_count: number; issues: ValidationIssue[]; }
export interface SkillProfile { id: string; name: string; description: string; role: string; task_kinds: string[]; tags: string[]; }
export interface PresetSummary { id: string; name: string; description: string; tags: string[]; }
export interface DryRunResult {
  dry_run: boolean; validation: ValidationResult; policy_summary: Record<string, unknown>;
  blueprint: unknown; step_count: number; edge_count: number;
  step_execution_plan?: StepExecutionPlan[];
  non_executable_count?: number;
  per_step_model_plan?: PerStepModelPlan[];
  model_routing_summary?: Record<string, unknown>;
}
export interface BpmnImportResult { graph: VpGraph; warnings: string[]; validation: ValidationResult; }
export interface BpmnExportResult { bpmn_xml: string; warnings: string[]; }
export interface WorkflowRequestResult { workflow_request: Record<string, unknown>; validation: ValidationResult; errors: string[]; }
export interface WorkflowStatus { schema: string; backend: string; workflow_id: string; status: string; steps?: unknown[]; events?: unknown[]; [key: string]: unknown; }
export interface TaskKindInfo {
  id: string; label: string; group: string; dispatch_capable: boolean; description: string;
  // Runtime-Truth (VPRT-001) — populated from backend, may be absent in fallback
  implementation_status?: string;   // "production"|"experimental"|"stub"|"test_only"|"design_only"|"unknown"
  implementation_state?: string;    // "wired_and_executable"|"registered_only"|"not_implemented"|...
  backend_service?: string;
  deterministic?: boolean;
  uses_llm?: boolean;
  uses_network?: boolean;
  side_effects?: string[];
  risk_level?: string;              // "none"|"low"|"medium"|"high"|"critical"
  legacy_aliases?: string[];
  requires_approval?: boolean;
}
export interface StepExecutionPlan {
  step_id: string; step_label: string; kind: string;
  executable: boolean;
  execution_mode: string;           // "worker_dispatch"|"vp_adapter"|"not_executable"
  execution_reason: string;
  implementation_state: string;
  implementation_status: string;
  backend_service: string;
  uses_llm: boolean; uses_network: boolean;
  side_effects: string[];
  risk_level: string;
  requires_approval: boolean;
}
export interface SavedGraphSummary { id: string; name: string; description: string; tags: string[]; updated_at: number; created_at: number; }

@Injectable({ providedIn: 'root' })
export class VisualProcessApiService {
  private http = inject(HttpClient);
  private dir  = inject(AgentDirectoryService);

  private get baseUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  // ── Presets ─────────────────────────────────────────────────────────────────

  listPresets(): Observable<PresetSummary[]> {
    return this.http.get<PresetSummary[]>(`${this.baseUrl}/api/visual-process/presets`);
  }

  getPreset(id: string): Observable<VpGraph> {
    return this.http.get<VpGraph>(`${this.baseUrl}/api/visual-process/presets/${id}`);
  }

  // ── Skill profiles ───────────────────────────────────────────────────────────

  listSkillProfiles(): Observable<SkillProfile[]> {
    return this.http.get<SkillProfile[]>(`${this.baseUrl}/api/visual-process/skill-profiles`);
  }

  // ── Task kinds (VPWRK-001) ──────────────────────────────────────────────────

  listTaskKinds(): Observable<TaskKindInfo[]> {
    return this.http.get<TaskKindInfo[]>(`${this.baseUrl}/api/visual-process/task-kinds`);
  }

  // ── Validation ───────────────────────────────────────────────────────────────

  validate(graph: VpGraph): Observable<ValidationResult> {
    return this.http.post<ValidationResult>(`${this.baseUrl}/api/visual-process/validate`, graph);
  }

  dryRun(graph: VpGraph): Observable<DryRunResult> {
    return this.http.post<DryRunResult>(`${this.baseUrl}/api/visual-process/dry-run`, graph);
  }

  listModelProfiles(): Observable<ModelRoutingProfilesResult> {
    return this.http.get<ModelRoutingProfilesResult>(`${this.baseUrl}/config/model-routing/profiles`);
  }

  getModelRoutingDiagnostics(): Observable<Record<string, unknown>> {
    return this.http.get<Record<string, unknown>>(`${this.baseUrl}/config/model-routing/read-model`);
  }

  validateModelRouting(graph: VpGraph): Observable<Record<string, unknown>> {
    return this.http.post<Record<string, unknown>>(`${this.baseUrl}/api/visual-process/model-routing/validate`, graph);
  }

  estimateModelCost(graph: VpGraph): Observable<Record<string, unknown>> {
    return this.http.post<Record<string, unknown>>(`${this.baseUrl}/api/visual-process/model-routing/estimate-cost`, graph);
  }

  // ── Mermaid ──────────────────────────────────────────────────────────────────

  mermaid(graph: VpGraph, direction: 'LR' | 'TD' = 'LR'): Observable<{ mermaid: string; tui?: string }> {
    return this.http.post<{ mermaid: string; tui?: string }>(
      `${this.baseUrl}/api/visual-process/mermaid`,
      { ...graph, direction, include_tui: true },
    );
  }

  // ── Policy ───────────────────────────────────────────────────────────────────

  policySummary(graph: VpGraph): Observable<{ summary: Record<string, unknown>; per_step: Record<string, string[]> }> {
    return this.http.post<any>(`${this.baseUrl}/api/visual-process/policy-summary`, graph);
  }

  // ── BPMN ─────────────────────────────────────────────────────────────────────

  importBpmn(bpmnXml: string): Observable<BpmnImportResult> {
    return this.http.post<BpmnImportResult>(`${this.baseUrl}/api/visual-process/bpmn/import`, { bpmn_xml: bpmnXml });
  }

  exportBpmn(graph: VpGraph): Observable<BpmnExportResult> {
    return this.http.post<BpmnExportResult>(`${this.baseUrl}/api/visual-process/bpmn/export`, graph);
  }

  // ── Workflow ─────────────────────────────────────────────────────────────────

  compileWorkflowRequest(graph: VpGraph, options: Record<string, unknown> = {}): Observable<WorkflowRequestResult> {
    return this.http.post<WorkflowRequestResult>(`${this.baseUrl}/api/visual-process/workflow-request`, { graph, ...options });
  }

  startWorkflowFromGraph(graph: VpGraph, options: Record<string, unknown> = {}): Observable<WorkflowStatus> {
    return this.http.post<WorkflowStatus>(`${this.baseUrl}/api/visual-process/workflow/start`, { graph, ...options });
  }

  getWorkflowStatus(workflowId: string): Observable<WorkflowStatus> {
    return this.http.get<WorkflowStatus>(`${this.baseUrl}/api/visual-process/workflow/${encodeURIComponent(workflowId)}/status`);
  }

  cancelWorkflow(workflowId: string, reason = ''): Observable<WorkflowStatus> {
    return this.http.post<WorkflowStatus>(
      `${this.baseUrl}/api/visual-process/workflow/${encodeURIComponent(workflowId)}/cancel`,
      { reason },
    );
  }

  signalWorkflow(workflowId: string, name: string, payload: Record<string, unknown> = {}): Observable<WorkflowStatus> {
    return this.http.post<WorkflowStatus>(
      `${this.baseUrl}/api/visual-process/workflow/${encodeURIComponent(workflowId)}/signal`,
      { name, payload, actor: 'visual_process_designer' },
    );
  }

  getWorkflowEvents(workflowId: string): Observable<{ events: Record<string, unknown>[] }> {
    return this.http.get<{ events: Record<string, unknown>[] }>(
      `${this.baseUrl}/api/visual-process/workflow/${encodeURIComponent(workflowId)}/events`,
    );
  }

  // ── Graph persistence (VPPERS-001) ───────────────────────────────────────────

  saveGraph(graph: VpGraph): Observable<{ id: string; saved: boolean }> {
    return this.http.post<{ id: string; saved: boolean }>(`${this.baseUrl}/api/visual-process/graphs`, graph);
  }

  listSavedGraphs(): Observable<SavedGraphSummary[]> {
    return this.http.get<SavedGraphSummary[]>(`${this.baseUrl}/api/visual-process/graphs`);
  }

  loadSavedGraph(id: string): Observable<VpGraph> {
    return this.http.get<VpGraph>(`${this.baseUrl}/api/visual-process/graphs/${encodeURIComponent(id)}`);
  }

  deleteSavedGraph(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/api/visual-process/graphs/${encodeURIComponent(id)}`);
  }

  // ── Blueprint (VPBLUEPR-001) ─────────────────────────────────────────────────

  saveAsBlueprint(graph: VpGraph): Observable<{ blueprint_id: string; saved: boolean }> {
    return this.http.post<{ blueprint_id: string; saved: boolean }>(`${this.baseUrl}/api/visual-process/save-blueprint`, graph);
  }
}
