import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, InjectionToken, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  VpAssistantLocation,
  VpEditorContextPayload,
  VpHelpResponse,
  VpWorkflowPatch,
} from './vp-editor-context.models';
import { ValidationIssue, VpGraph, VpRuntimeOverlay } from './visual-process-api.service';

export interface VpAssistantContextCreateRequest {
  graph_id: string;
  location: VpAssistantLocation;
  editor_mode: VpEditorContextPayload['editor_mode'];
  repository_revision: string;
  codecompass_manifest_hash: string;
  source_allowlist_version: string;
  source_scope: string;
  catalog_task_id?: string;
  catalog_id?: string;
  catalog_hash?: string;
  draft_graph?: VpGraph;
  runtime_overlay?: VpRuntimeOverlay;
  validation_issues?: ValidationIssue[];
  locale?: string;
}

export interface VpAssistantContextResource {
  context_id: string;
  graph_id: string;
  definition_revision: number;
  definition_hash: string;
  editor_mode: VpEditorContextPayload['editor_mode'];
  locale: string;
  context: VpEditorContextPayload;
  created_at: number;
}

export interface VpAssistantConversationResource {
  conversation_id: string;
  graph_id: string;
  status: string;
  active_context_id: string;
  created_at: number;
  updated_at: number;
  requests?: VpAssistantRequestResource[];
}

export type VpAssistantRequestStatus =
  | 'queued_retrieval'
  | 'retrieving'
  | 'queued_inference'
  | 'inferencing'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'timeout'
  | 'rejected';

export const VP_ASSISTANT_ACTIVE_STATUSES = new Set<VpAssistantRequestStatus>([
  'queued_retrieval', 'retrieving', 'queued_inference', 'inferencing',
]);

export const VP_ASSISTANT_RETRYABLE_STATUSES = new Set<VpAssistantRequestStatus>([
  'failed', 'cancelled', 'timeout', 'rejected',
]);

export interface VpAssistantRequestResource {
  request_id: string;
  conversation_id: string;
  context_id: string;
  prompt_context_id?: string | null;
  prompt_version: string;
  client_request_id: string;
  status: VpAssistantRequestStatus;
  retrieval_task_id?: string | null;
  inference_task_id?: string | null;
  response?: VpHelpResponse | null;
  error_code?: string | null;
  created_at: number;
  updated_at: number;
  cancelled_at?: number | null;
  refresh_of_request_id?: string;
  refresh_context_id?: string;
}

export interface VpAssistantCapabilities {
  contract_version: string;
  registry_inspector: boolean;
  hover_help: boolean;
  assistant_chat: boolean;
  ai_patches: boolean;
  limits: Record<string, number>;
}

export interface VpAssistantPatchPreview {
  patch_hash: string;
  base_graph_hash: string;
  input_draft_hash?: string;
  preview_graph_hash: string;
  preview_graph: VpGraph;
  validation: { valid: boolean; error_count: number; warning_count: number; issues: ValidationIssue[] };
  operation_count: number;
  audit_id: string;
  decision: string;
  [key: string]: unknown;
}

export interface VpAssistantPatchDecision {
  audit_id: string;
  request_id: string;
  patch_hash: string;
  decision: 'accepted' | 'rejected';
  apply_mode: 'local_editor_command_only' | 'none';
  preview: Record<string, unknown>;
}

export interface VpAssistantPatchRefreshRequest {
  draft_graph: VpGraph;
  validation_issues?: ValidationIssue[];
  runtime_overlay?: VpRuntimeOverlay;
  client_request_id: string;
}

/** Focused API boundary for Hub-owned Visual Process assistant orchestration. */
export interface VpAssistantApiPort {
  capabilities(): Observable<VpAssistantCapabilities>;
  createContext(request: VpAssistantContextCreateRequest): Observable<VpAssistantContextResource>;
  getContext(contextId: string): Observable<VpAssistantContextResource>;
  createConversation(contextId: string): Observable<VpAssistantConversationResource>;
  getConversation(conversationId: string): Observable<VpAssistantConversationResource>;
  switchConversationContext(conversationId: string, contextId: string): Observable<VpAssistantConversationResource>;
  submitQuestion(conversationId: string, question: string, clientRequestId: string, idempotencyKey: string): Observable<VpAssistantRequestResource>;
  getRequest(requestId: string): Observable<VpAssistantRequestResource>;
  cancelRequest(requestId: string): Observable<VpAssistantRequestResource>;
  retryRequest(requestId: string, clientRequestId: string, idempotencyKey: string): Observable<VpAssistantRequestResource>;
  previewPatch(requestId: string, patch?: VpWorkflowPatch, draftGraph?: VpGraph): Observable<VpAssistantPatchPreview>;
  refreshPatch(requestId: string, request: VpAssistantPatchRefreshRequest, idempotencyKey: string): Observable<VpAssistantRequestResource>;
  decidePatch(requestId: string, patchHash: string, decision: 'accepted' | 'rejected', confirmed: boolean, draftGraph?: VpGraph): Observable<VpAssistantPatchDecision>;
}

@Injectable({ providedIn: 'root' })
export class VpAssistantApiService implements VpAssistantApiPort {
  private readonly http = inject(HttpClient);
  private readonly directory = inject(AgentDirectoryService);

  private get baseUrl(): string {
    const hub = this.directory.list().find(agent => agent.role === 'hub')
      ?? this.directory.list().find(agent => agent.name === 'hub');
    return `${hub?.url ?? ''}/api/visual-process/assistant/v1`;
  }

  capabilities(): Observable<VpAssistantCapabilities> {
    return this.http.get<VpAssistantCapabilities>(`${this.baseUrl}/capabilities`);
  }

  createContext(request: VpAssistantContextCreateRequest): Observable<VpAssistantContextResource> {
    return this.http.post<VpAssistantContextResource>(`${this.baseUrl}/contexts`, request);
  }

  getContext(contextId: string): Observable<VpAssistantContextResource> {
    return this.http.get<VpAssistantContextResource>(`${this.baseUrl}/contexts/${encodeURIComponent(contextId)}`);
  }

  createConversation(contextId: string): Observable<VpAssistantConversationResource> {
    return this.http.post<VpAssistantConversationResource>(`${this.baseUrl}/conversations`, { context_id: contextId });
  }

  getConversation(conversationId: string): Observable<VpAssistantConversationResource> {
    return this.http.get<VpAssistantConversationResource>(`${this.baseUrl}/conversations/${encodeURIComponent(conversationId)}`);
  }

  switchConversationContext(conversationId: string, contextId: string): Observable<VpAssistantConversationResource> {
    return this.http.post<VpAssistantConversationResource>(
      `${this.baseUrl}/conversations/${encodeURIComponent(conversationId)}/context-switch`,
      { context_id: contextId, confirmed: true },
    );
  }

  submitQuestion(
    conversationId: string,
    question: string,
    clientRequestId: string,
    idempotencyKey: string,
  ): Observable<VpAssistantRequestResource> {
    return this.http.post<VpAssistantRequestResource>(
      `${this.baseUrl}/conversations/${encodeURIComponent(conversationId)}/questions`,
      { question, client_request_id: clientRequestId },
      { headers: new HttpHeaders({ 'Idempotency-Key': idempotencyKey }) },
    );
  }

  getRequest(requestId: string): Observable<VpAssistantRequestResource> {
    return this.http.get<VpAssistantRequestResource>(`${this.baseUrl}/requests/${encodeURIComponent(requestId)}`);
  }

  cancelRequest(requestId: string): Observable<VpAssistantRequestResource> {
    return this.http.post<VpAssistantRequestResource>(`${this.baseUrl}/requests/${encodeURIComponent(requestId)}/cancel`, {});
  }

  retryRequest(requestId: string, clientRequestId: string, idempotencyKey: string): Observable<VpAssistantRequestResource> {
    return this.http.post<VpAssistantRequestResource>(
      `${this.baseUrl}/requests/${encodeURIComponent(requestId)}/retry`,
      { client_request_id: clientRequestId },
      { headers: new HttpHeaders({ 'Idempotency-Key': idempotencyKey }) },
    );
  }

  previewPatch(requestId: string, patch?: VpWorkflowPatch, draftGraph?: VpGraph): Observable<VpAssistantPatchPreview> {
    return this.http.post<VpAssistantPatchPreview>(
      `${this.baseUrl}/requests/${encodeURIComponent(requestId)}/patch-preview`,
      { ...(patch ? { patch } : {}), ...(draftGraph ? { draft_graph: draftGraph } : {}) },
    );
  }

  refreshPatch(
    requestId: string,
    refreshRequest: VpAssistantPatchRefreshRequest,
    idempotencyKey: string,
  ): Observable<VpAssistantRequestResource> {
    return this.http.post<VpAssistantRequestResource>(
      `${this.baseUrl}/requests/${encodeURIComponent(requestId)}/patch-refresh`,
      refreshRequest,
      { headers: new HttpHeaders({ 'Idempotency-Key': idempotencyKey }) },
    );
  }

  decidePatch(
    requestId: string,
    patchHash: string,
    decision: 'accepted' | 'rejected',
    confirmed: boolean,
    draftGraph?: VpGraph,
  ): Observable<VpAssistantPatchDecision> {
    return this.http.post<VpAssistantPatchDecision>(
      `${this.baseUrl}/requests/${encodeURIComponent(requestId)}/patch-decisions`,
      { patch_hash: patchHash, decision, confirmed, ...(draftGraph ? { draft_graph: draftGraph } : {}) },
    );
  }
}

export const VP_ASSISTANT_API_PORT = new InjectionToken<VpAssistantApiPort>('VP_ASSISTANT_API_PORT', {
  providedIn: 'root',
  factory: () => inject(VpAssistantApiService),
});
