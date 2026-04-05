import { Injectable, inject } from '@angular/core';

import { HubApiService } from '../../services/hub-api.service';
import { HubLiveStateService } from '../../services/hub-live-state.service';

@Injectable({ providedIn: 'root' })
export class ControlPlaneFacade {
  private hubApi = inject(HubApiService);
  private liveState = inject(HubLiveStateService);

  ensureSystemEvents(hubUrl: string | undefined | null): void {
    this.liveState.ensureSystemEvents(hubUrl);
  }

  disconnectSystemEvents(): void {
    this.liveState.disconnectSystemEvents();
  }

  systemStreamConnected(): boolean {
    return this.liveState.systemStreamConnected();
  }

  lastSystemEvent(): any | null {
    return this.liveState.lastSystemEvent();
  }

  getDashboardReadModel(baseUrl: string, options?: { benchmarkTaskKind?: string; includeTaskSnapshot?: boolean }, token?: string, ttlMs?: number) {
    return this.hubApi.getDashboardReadModel(baseUrl, options as any, token as any, ttlMs);
  }

  getStatsHistory(baseUrl: string, token?: string) {
    return this.hubApi.getStatsHistory(baseUrl, token);
  }

  listTeams(baseUrl: string, token?: string) {
    return this.hubApi.listTeams(baseUrl, token);
  }

  listTeamRoles(baseUrl: string, token?: string) {
    return this.hubApi.listTeamRoles(baseUrl, token);
  }

  listAgents(baseUrl: string, token?: string) {
    return this.hubApi.listAgents(baseUrl, token);
  }

  getAutopilotStatus(baseUrl: string, token?: string) {
    return this.hubApi.getAutopilotStatus(baseUrl, token);
  }

  startAutopilot(
    baseUrl: string,
    body: {
      interval_seconds?: number;
      max_concurrency?: number;
      goal?: string;
      team_id?: string;
      budget_label?: string;
      security_level?: 'safe' | 'balanced' | 'aggressive';
    },
    token?: string,
  ) {
    return this.hubApi.startAutopilot(baseUrl, body, token);
  }

  stopAutopilot(baseUrl: string, token?: string) {
    return this.hubApi.stopAutopilot(baseUrl, token);
  }

  tickAutopilot(baseUrl: string, token?: string) {
    return this.hubApi.tickAutopilot(baseUrl, token);
  }

  getTaskTimeline(
    baseUrl: string,
    filters?: { team_id?: string; agent?: string; status?: string; error_only?: boolean; limit?: number },
    token?: string,
  ) {
    return this.hubApi.getTaskTimeline(baseUrl, filters, token);
  }

  getLlmBenchmarks(baseUrl: string, filters?: { task_kind?: string; top_n?: number }, token?: string) {
    return this.hubApi.getLlmBenchmarks(baseUrl, filters, token);
  }

  planGoal(baseUrl: string, body: { goal: string; context?: string; team_id?: string; parent_task_id?: string; create_tasks?: boolean }, token?: string) {
    return this.hubApi.planGoal(baseUrl, body, token);
  }

  getTaskOrchestrationReadModel(baseUrl: string, token?: string) {
    return this.hubApi.getTaskOrchestrationReadModel(baseUrl, token);
  }

  ingestOrchestrationTask(baseUrl: string, body: any, token?: string) {
    return this.hubApi.ingestOrchestrationTask(baseUrl, body, token);
  }

  claimOrchestrationTask(baseUrl: string, body: any, token?: string) {
    return this.hubApi.claimOrchestrationTask(baseUrl, body, token);
  }

  completeOrchestrationTask(baseUrl: string, body: any, token?: string) {
    return this.hubApi.completeOrchestrationTask(baseUrl, body, token);
  }

  listGoals(baseUrl: string, token?: string) {
    return this.hubApi.listGoals(baseUrl, token);
  }

  configureAutoPlanner(baseUrl: string, config: any, token?: string) {
    return this.hubApi.configureAutoPlanner(baseUrl, config, token);
  }

  createGoal(baseUrl: string, body: any, token?: string, timeoutMs?: number) {
    return this.hubApi.createGoal(baseUrl, body, token, timeoutMs);
  }

  getGoalDetail(baseUrl: string, goalId: string, token?: string) {
    return this.hubApi.getGoalDetail(baseUrl, goalId, token);
  }

  getGoalGovernanceSummary(baseUrl: string, goalId: string, token?: string) {
    return this.hubApi.getGoalGovernanceSummary(baseUrl, goalId, token);
  }

  patchGoalPlanNode(baseUrl: string, goalId: string, nodeId: string, patch: any, token?: string) {
    return this.hubApi.patchGoalPlanNode(baseUrl, goalId, nodeId, patch, token);
  }
}
