import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';

export interface EffectiveWorkflowRequest {
  surface: string;
  path?: string | null;
  task_kind?: string | null;
  include_readonly?: boolean;
  include_diagnostics?: boolean;
  include_alternatives?: boolean;
}

export interface EffectiveWorkflowNode {
  id: string;
  node_type: string;
  label: string;
  runtime_active: boolean;
  declared_value: unknown;
  effective_value: unknown;
  source_file: string | null;
  source_kind: string | null;
  source_pointer: string | null;
  writable: boolean;
  readonly_reason: string;
  reason: string;
  diagnostics: Array<Record<string, unknown>>;
  edit_target: Record<string, unknown>;
  data: Record<string, unknown>;
}

export interface EffectiveWorkflowEdge {
  source: string;
  target: string;
  edge_type: string;
  label: string;
  reason: string;
  data: Record<string, unknown>;
}

export interface EffectiveWorkflowResult {
  schema: string;
  request: EffectiveWorkflowRequest;
  summary: string;
  status: 'ok' | 'warning' | 'blocked' | string;
  effective_chain: EffectiveWorkflowNode[];
  graph: {
    nodes: Record<string, EffectiveWorkflowNode>;
    edges: EffectiveWorkflowEdge[];
  };
  selected: Record<string, unknown>;
  alternatives: Record<string, unknown>;
  blocked: Array<Record<string, unknown>>;
  warnings: Array<Record<string, unknown>>;
  explanation_trace: Array<Record<string, unknown>>;
  edit_links: Array<Record<string, unknown>>;
  source_index: Array<Record<string, unknown>>;
}

export interface EffectiveWorkflowOptions {
  schema: string;
  surfaces: string[];
  task_kinds: string[];
  path_suggestions: string[];
  workers: string[];
  blueprints: Array<Record<string, unknown>>;
}

export interface EffectiveWorkflowCompareResult {
  schema: string;
  left_request: EffectiveWorkflowRequest;
  right_request: EffectiveWorkflowRequest;
  status: 'same' | 'changed' | string;
  differences: Array<Record<string, unknown>>;
  left_summary: string;
  right_summary: string;
}

@Injectable({ providedIn: 'root' })
export class EffectiveWorkflowService {
  private readonly http = inject(HttpClient);
  private readonly dir = inject(AgentDirectoryService);

  private get baseUrl(): string {
    const hub = this.dir.list().find(agent => agent.role === 'hub');
    const origin = hub?.url ?? 'http://127.0.0.1:5000';
    return `${origin}/api/effective-workflow`;
  }

  getOptions(): Observable<EffectiveWorkflowOptions> {
    return this.http.get<EffectiveWorkflowOptions>(`${this.baseUrl}/options`);
  }

  resolve(request: EffectiveWorkflowRequest): Observable<EffectiveWorkflowResult> {
    return this.http.post<EffectiveWorkflowResult>(`${this.baseUrl}/resolve`, request);
  }

  compare(
    left: EffectiveWorkflowRequest,
    right: EffectiveWorkflowRequest,
  ): Observable<EffectiveWorkflowCompareResult> {
    return this.http.post<EffectiveWorkflowCompareResult>(`${this.baseUrl}/compare`, { left, right });
  }

  explainNode(
    request: EffectiveWorkflowRequest,
    nodeId: string,
  ): Observable<Record<string, unknown>> {
    return this.http.post<Record<string, unknown>>(`${this.baseUrl}/explain-node`, {
      request,
      node_id: nodeId,
    });
  }
}
