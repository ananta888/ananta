import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from '../../../services/hub-api-core.service';

export interface CcProjectReadModel {
  id: string;
  name: string;
  description: string;
  is_active: boolean;
  root: string | null;
}

export interface CcTaskReadModel {
  id: string;
  title: string;
  description: string;
  status: string;
  priority: string;
  project_id?: string | null;
  verification_status?: Record<string, unknown>;
}

export interface CcSessionReadModel {
  id: string;
  task_id: string | null;
  title: string;
  status: string;
  transport: string;
  mode: string;
  owner_user_id: string;
  session_kind?: string;
  worker_id?: string | null;
  worker_type?: string | null;
  model?: string | null;
  runtime?: string | null;
  policy_snapshot_id?: string | null;
  policy_snapshot?: {
    policy_version: string;
    risk_level: string;
    allowed_tools: string[];
    denied_tools: string[];
    allowed_paths: string[];
    denied_paths: string[];
    cloud_allowed: boolean;
    runtime_boundary: 'local-only' | 'cloud-allowed' | 'remote' | 'unknown' | string;
    requires_human_approval: boolean;
    approval_reason?: string | null;
  } | null;
}

export interface CcWorkerReadModel {
  id: string;
  runtime: string;
  health: string;
  capabilities: string[];
  boundary: string;
}

export interface CcPolicyDecisionReadModel {
  id: string;
  decision: 'allow' | 'deny' | 'require_approval' | string;
  decision_type: string;
  reason: string;
  matched_rule_ids: string[];
  created_at: number;
  action_id?: string;
  tool_call_id?: string;
}

export interface CcToolCallReadModel {
  id: string;
  session_id: string;
  task_id?: string | null;
  action_id: string;
  tool_name: string;
  status: string;
  risk_level: string;
  target_path?: string | null;
  created_at: number;
  started_at?: number | null;
  finished_at?: number | null;
  error_message?: string | null;
}

export interface CcTaskDetailReadModel {
  task: Record<string, unknown>;
  verification?: {
    status?: string;
    test_count?: number;
    passed_count?: number;
    failed_count?: number;
  };
}

export interface CcArtifactReadModel {
  id: string;
  latest_media_type?: string | null;
  latest_filename?: string | null;
  artifact_metadata?: Record<string, unknown>;
}

@Injectable({ providedIn: 'root' })
export class HubControlCenterApiClient {
  private core = inject(HubApiCoreService);

  listProjects(baseUrl: string, token?: string): Observable<{ items: CcProjectReadModel[]; count: number }> {
    return this.core.get<{ items: CcProjectReadModel[]; count: number }>(`${baseUrl}/api/projects`, baseUrl, token, false);
  }

  listProjectTasks(baseUrl: string, projectId: string, token?: string): Observable<{ items: CcTaskReadModel[]; count: number }> {
    return this.core.get<{ items: CcTaskReadModel[]; count: number }>(`${baseUrl}/api/projects/${encodeURIComponent(projectId)}/tasks`, baseUrl, token, false);
  }

  listSessions(baseUrl: string, taskId?: string, token?: string): Observable<{ items: CcSessionReadModel[]; count: number }> {
    const q = taskId ? `?task_id=${encodeURIComponent(taskId)}` : '';
    return this.core.get<{ items: CcSessionReadModel[]; count: number }>(`${baseUrl}/api/sessions${q}`, baseUrl, token, false);
  }

  listWorkers(baseUrl: string, token?: string): Observable<{ items: CcWorkerReadModel[]; count: number }> {
    return this.core.get<{ items: CcWorkerReadModel[]; count: number }>(`${baseUrl}/api/workers`, baseUrl, token, false);
  }

  listSessionPolicyDecisions(baseUrl: string, sessionId: string, token?: string): Observable<{ items: CcPolicyDecisionReadModel[]; count: number }> {
    return this.core.get<{ items: CcPolicyDecisionReadModel[]; count: number }>(
      `${baseUrl}/api/sessions/${encodeURIComponent(sessionId)}/policy-decisions`,
      baseUrl,
      token,
      false,
    );
  }

  listSessionToolCalls(baseUrl: string, sessionId: string, token?: string): Observable<{ items: CcToolCallReadModel[]; count: number }> {
    return this.core.get<{ items: CcToolCallReadModel[]; count: number }>(
      `${baseUrl}/api/sessions/${encodeURIComponent(sessionId)}/tool-calls`,
      baseUrl,
      token,
      false,
    );
  }

  approvePolicyAction(
    baseUrl: string,
    payload: { action_id: string; tool_call_id: string; session_id: string; scope: 'single_action' },
    token?: string,
  ): Observable<Record<string, unknown>> {
    return this.core.post<Record<string, unknown>>(`${baseUrl}/api/policy/approve`, payload, baseUrl, token, false);
  }

  listArtifacts(
    baseUrl: string,
    filters?: { project_id?: string; task_id?: string; session_id?: string; type?: string; limit?: number; offset?: number },
    token?: string,
  ): Observable<CcArtifactReadModel[]> {
    const params = new URLSearchParams();
    if (filters?.project_id) params.set('project_id', filters.project_id);
    if (filters?.task_id) params.set('task_id', filters.task_id);
    if (filters?.session_id) params.set('session_id', filters.session_id);
    if (filters?.type && filters.type !== 'all') params.set('type', filters.type);
    if (typeof filters?.limit === 'number') params.set('limit', String(filters.limit));
    if (typeof filters?.offset === 'number') params.set('offset', String(filters.offset));
    const query = params.toString();
    const url = `${baseUrl}/artifacts${query ? `?${query}` : ''}`;
    return this.core.get<CcArtifactReadModel[]>(url, baseUrl, token, false);
  }

  getArtifactContentNormalized(
    baseUrl: string,
    artifactId: string,
    offset = 0,
    limit = 131072,
    token?: string,
  ): Observable<{ type: string; encoding: string; payload: string; has_more: boolean; next_offset: number | null }> {
    return this.core.get<{ type: string; encoding: string; payload: string; has_more: boolean; next_offset: number | null }>(
      `${baseUrl}/artifacts/${encodeURIComponent(artifactId)}/content?normalized=true&offset=${offset}&limit=${limit}`,
      baseUrl,
      token,
      false,
    );
  }

  getTaskDetail(baseUrl: string, taskId: string, token?: string): Observable<CcTaskDetailReadModel> {
    return this.core.get<CcTaskDetailReadModel>(`${baseUrl}/api/tasks/${encodeURIComponent(taskId)}`, baseUrl, token, false);
  }

  createEventStreamToken(
    baseUrl: string,
    payload?: { project_id?: string; session_id?: string },
    token?: string,
  ): Observable<{ stream_token?: string; token?: string; expires_at: number; ttl_seconds: number }> {
    return this.core.post<{ stream_token?: string; token?: string; expires_at: number; ttl_seconds: number }>(
      `${baseUrl}/api/events/stream-token`,
      payload || {},
      baseUrl,
      token,
      false,
    );
  }
}
