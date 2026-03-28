var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { HubApiCoreService } from './hub-api-core.service';
let HubTasksApiClient = class HubTasksApiClient {
    constructor() {
        this.core = inject(HubApiCoreService);
    }
    listTasks(baseUrl, token) { return this.core.get(`${baseUrl}/tasks`, baseUrl, token, true); }
    getTask(baseUrl, id, token) { return this.core.get(`${baseUrl}/tasks/${id}`, baseUrl, token, true); }
    createTask(baseUrl, body, token) { return this.core.post(`${baseUrl}/tasks`, body, baseUrl, token); }
    patchTask(baseUrl, id, patch, token) { return this.core.patch(`${baseUrl}/tasks/${id}`, patch, baseUrl, token); }
    assign(baseUrl, id, body, token) { return this.core.post(`${baseUrl}/tasks/${id}/assign`, body, baseUrl, token); }
    propose(baseUrl, id, body, token) { return this.core.post(`${baseUrl}/tasks/${id}/step/propose`, body, baseUrl, token, false, 60000); }
    execute(baseUrl, id, body, token) { return this.core.post(`${baseUrl}/tasks/${id}/step/execute`, body, baseUrl, token, false, 120000); }
    reviewTaskProposal(baseUrl, id, body, token) { return this.core.post(`${baseUrl}/tasks/${id}/review`, body, baseUrl, token); }
    getTaskTimeline(baseUrl, filters, token) {
        const q = new URLSearchParams();
        if (filters?.team_id)
            q.set('team_id', filters.team_id);
        if (filters?.agent)
            q.set('agent', filters.agent);
        if (filters?.status)
            q.set('status', filters.status);
        if (typeof filters?.error_only === 'boolean')
            q.set('error_only', filters.error_only ? '1' : '0');
        q.set('limit', String(filters?.limit || 200));
        const query = q.toString();
        return this.core.get(`${baseUrl}/tasks/timeline${query ? `?${query}` : ''}`, baseUrl, token, true);
    }
    getTaskOrchestrationReadModel(baseUrl, token) { return this.core.get(`${baseUrl}/tasks/orchestration/read-model`, baseUrl, token, true); }
    ingestOrchestrationTask(baseUrl, body, token) { return this.core.post(`${baseUrl}/tasks/orchestration/ingest`, body, baseUrl, token); }
    claimOrchestrationTask(baseUrl, body, token) { return this.core.post(`${baseUrl}/tasks/orchestration/claim`, body, baseUrl, token); }
    completeOrchestrationTask(baseUrl, body, token) { return this.core.post(`${baseUrl}/tasks/orchestration/complete`, body, baseUrl, token); }
    listGoals(baseUrl, token) { return this.core.get(`${baseUrl}/goals`, baseUrl, token, true); }
    getGoal(baseUrl, id, token) { return this.core.get(`${baseUrl}/goals/${id}`, baseUrl, token, true); }
    getGoalDetail(baseUrl, id, token) { return this.core.get(`${baseUrl}/goals/${id}/detail`, baseUrl, token, true); }
    getGoalPlan(baseUrl, id, token) { return this.core.get(`${baseUrl}/goals/${id}/plan`, baseUrl, token, true); }
    patchGoalPlanNode(baseUrl, goalId, nodeId, patch, token) {
        return this.core.patch(`${baseUrl}/goals/${goalId}/plan/nodes/${nodeId}`, patch, baseUrl, token);
    }
    getGoalGovernanceSummary(baseUrl, goalId, token) {
        return this.core.get(`${baseUrl}/goals/${goalId}/governance-summary`, baseUrl, token, true);
    }
    createGoal(baseUrl, body, token) { return this.core.post(`${baseUrl}/goals`, body, baseUrl, token, false, 60000); }
    taskLogs(baseUrl, id, token) { return this.core.get(`${baseUrl}/tasks/${id}/logs`, baseUrl, token, true); }
    streamTaskLogs(baseUrl, id, token) { return this.core.streamTaskLogs(baseUrl, id, token); }
    listArchivedTasks(baseUrl, token, limit = 100, offset = 0) {
        const q = new URLSearchParams();
        q.set('limit', String(limit));
        q.set('offset', String(offset));
        return this.core.get(`${baseUrl}/tasks/archived?${q.toString()}`, baseUrl, token, true);
    }
    archiveTask(baseUrl, id, token) { return this.core.post(`${baseUrl}/tasks/${id}/archive`, {}, baseUrl, token); }
    restoreTask(baseUrl, id, token) { return this.core.post(`${baseUrl}/tasks/archived/${id}/restore`, {}, baseUrl, token); }
    cleanupTasks(baseUrl, body, token) { return this.core.post(`${baseUrl}/tasks/cleanup`, body, baseUrl, token); }
    cleanupArchivedTasks(baseUrl, body, token) { return this.core.post(`${baseUrl}/tasks/archived/cleanup`, body, baseUrl, token); }
    deleteArchivedTask(baseUrl, id, token) { return this.core.delete(`${baseUrl}/tasks/archived/${id}`, baseUrl, token); }
};
HubTasksApiClient = __decorate([
    Injectable({ providedIn: 'root' })
], HubTasksApiClient);
export { HubTasksApiClient };
//# sourceMappingURL=hub-tasks-api.client.js.map