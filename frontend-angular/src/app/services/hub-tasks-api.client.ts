import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import { GoalGovernanceSummary, TaskOrchestrationReadModel } from '../models/dashboard.models';

@Injectable({ providedIn: 'root' })
export class HubTasksApiClient {
  private core = inject(HubApiCoreService);

  listTasks(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/tasks`, baseUrl, token, true); }
  getTask(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.get(`${baseUrl}/tasks/${id}`, baseUrl, token, true); }
  createTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks`, body, baseUrl, token); }
  patchTask(baseUrl: string, id: string, patch: any, token?: string): Observable<any> { return this.core.patch(`${baseUrl}/tasks/${id}`, patch, baseUrl, token); }
  assign(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/${id}/assign`, body, baseUrl, token); }
  propose(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/${id}/step/propose`, body, baseUrl, token, false, 60000); }
  execute(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/${id}/step/execute`, body, baseUrl, token, false, 120000); }
  reviewTaskProposal(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/${id}/review`, body, baseUrl, token); }

  getTaskTimeline(baseUrl: string, filters?: { team_id?: string; agent?: string; status?: string; error_only?: boolean; limit?: number }, token?: string): Observable<any> {
    const q = new URLSearchParams();
    if (filters?.team_id) q.set('team_id', filters.team_id);
    if (filters?.agent) q.set('agent', filters.agent);
    if (filters?.status) q.set('status', filters.status);
    if (typeof filters?.error_only === 'boolean') q.set('error_only', filters.error_only ? '1' : '0');
    q.set('limit', String(filters?.limit || 200));
    const query = q.toString();
    return this.core.get(`${baseUrl}/tasks/timeline${query ? `?${query}` : ''}`, baseUrl, token, true);
  }

  getTaskOrchestrationReadModel(baseUrl: string, token?: string): Observable<TaskOrchestrationReadModel> {
    return this.core.get<TaskOrchestrationReadModel>(`${baseUrl}/tasks/orchestration/read-model`, baseUrl, token, true);
  }
  ingestOrchestrationTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post<any>(`${baseUrl}/tasks/orchestration/ingest`, body, baseUrl, token); }
  claimOrchestrationTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post<any>(`${baseUrl}/tasks/orchestration/claim`, body, baseUrl, token); }
  completeOrchestrationTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post<any>(`${baseUrl}/tasks/orchestration/complete`, body, baseUrl, token); }
  listGoals(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/goals`, baseUrl, token, true); }
  getGoalModes(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/goals/modes`, baseUrl, token, true); }
  getGoal(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/goals/${id}`, baseUrl, token, true); }
  getGoalDetail(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/goals/${id}/detail`, baseUrl, token, true); }
  getGoalPlan(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/goals/${id}/plan`, baseUrl, token, true); }
  patchGoalPlanNode(baseUrl: string, goalId: string, nodeId: string, patch: any, token?: string): Observable<any> {
    return this.core.patch<any>(`${baseUrl}/goals/${goalId}/plan/nodes/${nodeId}`, patch, baseUrl, token);
  }
  getGoalGovernanceSummary(baseUrl: string, goalId: string, token?: string): Observable<GoalGovernanceSummary> {
    return this.core.get<GoalGovernanceSummary>(`${baseUrl}/goals/${goalId}/governance-summary`, baseUrl, token, true);
  }
  createGoal(baseUrl: string, body: any, token?: string, timeoutMs = 180000): Observable<any> {
    return this.core.post<any>(`${baseUrl}/goals`, body, baseUrl, token, false, timeoutMs);
  }

  getInstructionLayerModel(baseUrl: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/instruction-layers/model`, baseUrl, token, true);
  }
  getInstructionLayersEffective(
    baseUrl: string,
    params?: {
      owner_username?: string;
      task_id?: string;
      goal_id?: string;
      session_id?: string;
      usage_key?: string;
      base_prompt?: string;
      profile_id?: string;
      overlay_id?: string;
    },
    token?: string,
  ): Observable<any> {
    const q = new URLSearchParams();
    if (params?.owner_username) q.set('owner_username', params.owner_username);
    if (params?.task_id) q.set('task_id', params.task_id);
    if (params?.goal_id) q.set('goal_id', params.goal_id);
    if (params?.session_id) q.set('session_id', params.session_id);
    if (params?.usage_key) q.set('usage_key', params.usage_key);
    if (params?.base_prompt) q.set('base_prompt', params.base_prompt);
    if (params?.profile_id) q.set('profile_id', params.profile_id);
    if (params?.overlay_id) q.set('overlay_id', params.overlay_id);
    const query = q.toString();
    return this.core.get<any>(`${baseUrl}/instruction-layers/effective${query ? `?${query}` : ''}`, baseUrl, token, true, 60000);
  }
  listInstructionProfiles(baseUrl: string, ownerUsername?: string, token?: string): Observable<any[]> {
    const q = new URLSearchParams();
    if (ownerUsername) q.set('owner_username', ownerUsername);
    const query = q.toString();
    return this.core.get<any[]>(`${baseUrl}/instruction-profiles${query ? `?${query}` : ''}`, baseUrl, token, true);
  }
  listInstructionProfileExamples(baseUrl: string, token?: string): Observable<any[]> {
    return this.core.get<any[]>(`${baseUrl}/instruction-profiles/examples`, baseUrl, token, true);
  }
  createInstructionProfile(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/instruction-profiles`, body, baseUrl, token);
  }
  patchInstructionProfile(baseUrl: string, profileId: string, body: any, token?: string): Observable<any> {
    return this.core.patch<any>(`${baseUrl}/instruction-profiles/${profileId}`, body, baseUrl, token);
  }
  deleteInstructionProfile(baseUrl: string, profileId: string, token?: string): Observable<any> {
    return this.core.delete<any>(`${baseUrl}/instruction-profiles/${profileId}`, baseUrl, token);
  }
  selectInstructionProfile(baseUrl: string, profileId: string, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/instruction-profiles/${profileId}/select`, {}, baseUrl, token);
  }

  listInstructionOverlays(
    baseUrl: string,
    filters?: { owner_username?: string; attachment_kind?: string; attachment_id?: string },
    token?: string,
  ): Observable<any[]> {
    const q = new URLSearchParams();
    if (filters?.owner_username) q.set('owner_username', filters.owner_username);
    if (filters?.attachment_kind) q.set('attachment_kind', filters.attachment_kind);
    if (filters?.attachment_id) q.set('attachment_id', filters.attachment_id);
    const query = q.toString();
    return this.core.get<any[]>(`${baseUrl}/instruction-overlays${query ? `?${query}` : ''}`, baseUrl, token, true);
  }
  createInstructionOverlay(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/instruction-overlays`, body, baseUrl, token);
  }
  patchInstructionOverlay(baseUrl: string, overlayId: string, body: any, token?: string): Observable<any> {
    return this.core.patch<any>(`${baseUrl}/instruction-overlays/${overlayId}`, body, baseUrl, token);
  }
  deleteInstructionOverlay(baseUrl: string, overlayId: string, token?: string): Observable<any> {
    return this.core.delete<any>(`${baseUrl}/instruction-overlays/${overlayId}`, baseUrl, token);
  }
  selectInstructionOverlay(baseUrl: string, overlayId: string, body?: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/instruction-overlays/${overlayId}/select`, body || {}, baseUrl, token);
  }
  attachInstructionOverlay(baseUrl: string, overlayId: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/instruction-overlays/${overlayId}/attach`, body, baseUrl, token);
  }
  detachInstructionOverlay(baseUrl: string, overlayId: string, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/instruction-overlays/${overlayId}/detach`, {}, baseUrl, token);
  }
  setGoalInstructionSelection(baseUrl: string, goalId: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/goals/${goalId}/instruction-selection`, body, baseUrl, token);
  }
  setTaskInstructionSelection(baseUrl: string, taskId: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/tasks/${taskId}/instruction-selection`, body, baseUrl, token);
  }

  taskLogs(baseUrl: string, id: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/tasks/${id}/logs`, baseUrl, token, true); }
  streamTaskLogs(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.streamTaskLogs(baseUrl, id, token); }

  listArchivedTasks(baseUrl: string, token?: string, limit = 100, offset = 0): Observable<any[]> {
    const q = new URLSearchParams();
    q.set('limit', String(limit));
    q.set('offset', String(offset));
    return this.core.get<any[]>(`${baseUrl}/tasks/archived?${q.toString()}`, baseUrl, token, true);
  }
  archiveTask(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/${id}/archive`, {}, baseUrl, token); }
  restoreTask(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/archived/${id}/restore`, {}, baseUrl, token); }
  cleanupTasks(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/cleanup`, body, baseUrl, token); }
  cleanupArchivedTasks(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/archived/cleanup`, body, baseUrl, token); }
  deleteArchivedTask(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.delete(`${baseUrl}/tasks/archived/${id}`, baseUrl, token); }
}
