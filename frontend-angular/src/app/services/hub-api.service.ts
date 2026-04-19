import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubTemplatesApiClient } from './hub-templates-api.client';
import { HubConfigApiClient } from './hub-config-api.client';
import { HubTasksApiClient } from './hub-tasks-api.client';
import { HubSystemApiClient } from './hub-system-api.client';
import { HubTeamsApiClient } from './hub-teams-api.client';
import { HubAutomationApiClient } from './hub-automation-api.client';
import { HubArtifactsApiClient } from './hub-artifacts-api.client';
import { HubKnowledgeApiClient } from './hub-knowledge-api.client';

@Injectable({ providedIn: 'root' })
export class HubApiService {
  private templates = inject(HubTemplatesApiClient);
  private config = inject(HubConfigApiClient);
  private tasks = inject(HubTasksApiClient);
  private system = inject(HubSystemApiClient);
  private teams = inject(HubTeamsApiClient);
  private automation = inject(HubAutomationApiClient);
  private artifacts = inject(HubArtifactsApiClient);
  private knowledge = inject(HubKnowledgeApiClient);

  listTemplates(baseUrl: string, token?: string): Observable<any[]> { return this.templates.listTemplates(baseUrl, token); }
  createTemplate(baseUrl: string, tpl: any, token?: string): Observable<any> { return this.templates.createTemplate(baseUrl, tpl, token); }
  updateTemplate(baseUrl: string, id: string, patch: any, token?: string): Observable<any> { return this.templates.updateTemplate(baseUrl, id, patch, token); }
  deleteTemplate(baseUrl: string, id: string, token?: string): Observable<any> { return this.templates.deleteTemplate(baseUrl, id, token); }

  getConfig(baseUrl: string, token?: string): Observable<any> { return this.config.getConfig(baseUrl, token); }
  getAssistantReadModel(baseUrl: string, token?: string): Observable<any> { return this.config.getAssistantReadModel(baseUrl, token); }
  getDashboardReadModel(
    baseUrl: string,
    optionsOrToken?: { benchmarkTaskKind?: string; ttlMs?: number; includeTaskSnapshot?: boolean } | string,
    tokenOrTtlMs?: string | number,
    ttlMs?: number,
  ): Observable<any> {
    return this.config.getDashboardReadModel(baseUrl, optionsOrToken as any, tokenOrTtlMs as any, ttlMs);
  }
  setConfig(baseUrl: string, cfg: any, token?: string): Observable<any> { return this.config.setConfig(baseUrl, cfg, token); }
  listProviders(baseUrl: string, token?: string): Observable<any[]> { return this.config.listProviders(baseUrl, token); }
  listProviderCatalog(baseUrl: string, token?: string): Observable<any> { return this.config.listProviderCatalog(baseUrl, token); }
  getLlmBenchmarks(baseUrl: string, filters?: { task_kind?: string; top_n?: number }, token?: string): Observable<any> { return this.config.getLlmBenchmarks(baseUrl, filters, token); }
  getLlmBenchmarksConfig(baseUrl: string, token?: string): Observable<any> { return this.config.getLlmBenchmarksConfig(baseUrl, token); }

  listTasks(baseUrl: string, token?: string): Observable<any[]> { return this.tasks.listTasks(baseUrl, token); }
  getTask(baseUrl: string, id: string, token?: string): Observable<any> { return this.tasks.getTask(baseUrl, id, token); }
  createTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.tasks.createTask(baseUrl, body, token); }
  patchTask(baseUrl: string, id: string, patch: any, token?: string): Observable<any> { return this.tasks.patchTask(baseUrl, id, patch, token); }
  assign(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.tasks.assign(baseUrl, id, body, token); }
  propose(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.tasks.propose(baseUrl, id, body, token); }
  execute(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.tasks.execute(baseUrl, id, body, token); }
  getTaskTimeline(baseUrl: string, filters?: { team_id?: string; agent?: string; status?: string; error_only?: boolean; limit?: number }, token?: string): Observable<any> { return this.tasks.getTaskTimeline(baseUrl, filters, token); }
  getTaskOrchestrationReadModel(baseUrl: string, token?: string): Observable<any> { return this.tasks.getTaskOrchestrationReadModel(baseUrl, token); }
  ingestOrchestrationTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.tasks.ingestOrchestrationTask(baseUrl, body, token); }
  claimOrchestrationTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.tasks.claimOrchestrationTask(baseUrl, body, token); }
  completeOrchestrationTask(baseUrl: string, body: any, token?: string): Observable<any> { return this.tasks.completeOrchestrationTask(baseUrl, body, token); }
  listGoals(baseUrl: string, token?: string): Observable<any[]> { return this.tasks.listGoals(baseUrl, token); }
  listGoalModes(baseUrl: string, token?: string): Observable<any[]> { return this.tasks.getGoalModes(baseUrl, token); }
  getGoal(baseUrl: string, id: string, token?: string): Observable<any> { return this.tasks.getGoal(baseUrl, id, token); }
  getGoalDetail(baseUrl: string, id: string, token?: string): Observable<any> { return this.tasks.getGoalDetail(baseUrl, id, token); }
  getGoalPlan(baseUrl: string, id: string, token?: string): Observable<any> { return this.tasks.getGoalPlan(baseUrl, id, token); }
  patchGoalPlanNode(baseUrl: string, goalId: string, nodeId: string, patch: any, token?: string): Observable<any> {
    return this.tasks.patchGoalPlanNode(baseUrl, goalId, nodeId, patch, token);
  }
  getGoalGovernanceSummary(baseUrl: string, goalId: string, token?: string): Observable<any> { return this.tasks.getGoalGovernanceSummary(baseUrl, goalId, token); }
  createGoal(baseUrl: string, body: any, token?: string, timeoutMs?: number): Observable<any> {
    return this.tasks.createGoal(baseUrl, body, token, timeoutMs);
  }
  taskLogs(baseUrl: string, id: string, token?: string): Observable<any[]> { return this.tasks.taskLogs(baseUrl, id, token); }
  streamTaskLogs(baseUrl: string, id: string, token?: string): Observable<any> { return this.tasks.streamTaskLogs(baseUrl, id, token); }
  listArchivedTasks(baseUrl: string, token?: string, limit = 100, offset = 0): Observable<any[]> {
    return this.tasks.listArchivedTasks(baseUrl, token, limit, offset);
  }
  archiveTask(baseUrl: string, id: string, token?: string): Observable<any> { return this.tasks.archiveTask(baseUrl, id, token); }
  restoreTask(baseUrl: string, id: string, token?: string): Observable<any> { return this.tasks.restoreTask(baseUrl, id, token); }
  cleanupTasks(baseUrl: string, body: any, token?: string): Observable<any> { return this.tasks.cleanupTasks(baseUrl, body, token); }
  cleanupArchivedTasks(baseUrl: string, body: any, token?: string): Observable<any> { return this.tasks.cleanupArchivedTasks(baseUrl, body, token); }
  deleteArchivedTask(baseUrl: string, id: string, token?: string): Observable<any> { return this.tasks.deleteArchivedTask(baseUrl, id, token); }
  reviewTaskProposal(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.tasks.reviewTaskProposal(baseUrl, id, body, token); }

  listArtifacts(baseUrl: string, token?: string): Observable<any[]> { return this.artifacts.listArtifacts(baseUrl, token); }
  getArtifact(baseUrl: string, artifactId: string, token?: string): Observable<any> { return this.artifacts.getArtifact(baseUrl, artifactId, token); }
  uploadArtifact(baseUrl: string, file: File, collectionName?: string, token?: string): Observable<any> {
    return this.artifacts.uploadArtifact(baseUrl, file, collectionName, token);
  }
  extractArtifact(baseUrl: string, artifactId: string, token?: string): Observable<any> { return this.artifacts.extractArtifact(baseUrl, artifactId, token); }
  indexArtifact(baseUrl: string, artifactId: string, body?: any, token?: string): Observable<any> { return this.artifacts.indexArtifact(baseUrl, artifactId, body, token); }
  getArtifactRagStatus(baseUrl: string, artifactId: string, token?: string): Observable<any> { return this.artifacts.getArtifactRagStatus(baseUrl, artifactId, token); }
  getArtifactRagPreview(baseUrl: string, artifactId: string, limit = 5, token?: string): Observable<any> {
    return this.artifacts.getArtifactRagPreview(baseUrl, artifactId, limit, token);
  }
  listKnowledgeCollections(baseUrl: string, token?: string): Observable<any[]> { return this.knowledge.listCollections(baseUrl, token); }
  listKnowledgeIndexProfiles(baseUrl: string, token?: string): Observable<any> { return this.knowledge.listIndexProfiles(baseUrl, token); }
  createKnowledgeCollection(baseUrl: string, payload: { name: string; description?: string }, token?: string): Observable<any> {
    return this.knowledge.createCollection(baseUrl, payload, token);
  }
  getKnowledgeCollection(baseUrl: string, collectionId: string, token?: string): Observable<any> { return this.knowledge.getCollection(baseUrl, collectionId, token); }
  indexKnowledgeCollection(baseUrl: string, collectionId: string, body?: any, token?: string): Observable<any> { return this.knowledge.indexCollection(baseUrl, collectionId, body, token); }
  searchKnowledgeCollection(baseUrl: string, collectionId: string, payload: { query: string; top_k?: number }, token?: string): Observable<any> {
    return this.knowledge.searchCollection(baseUrl, collectionId, payload, token);
  }

  streamSystemEvents(baseUrl: string, token?: string): Observable<any> { return this.system.streamSystemEvents(baseUrl, token); }
  getSystemHealth(baseUrl: string, token?: string): Observable<any> { return this.system.getHealth(baseUrl, token); }
  getSystemContracts(baseUrl: string, token?: string): Observable<any> { return this.system.getContracts(baseUrl, token); }
  getDemoPreview(baseUrl: string, token?: string): Observable<any> { return this.system.getDemoPreview(baseUrl, token); }
  listAgents(baseUrl: string, token?: string): Observable<any> { return this.system.listAgents(baseUrl, token); }
  getStats(baseUrl: string, token?: string): Observable<any> { return this.system.getStats(baseUrl, token); }
  getStatsHistory(baseUrl: string, token?: string): Observable<any[]> { return this.system.getStatsHistory(baseUrl, token); }
  getAuditLogs(baseUrl: string, limit = 100, offset = 0, token?: string): Observable<any[]> { return this.system.getAuditLogs(baseUrl, limit, offset, token); }
  analyzeAuditLogs(baseUrl: string, limit = 50, token?: string): Observable<any> { return this.system.analyzeAuditLogs(baseUrl, limit, token); }

  listTeams(baseUrl: string, token?: string): Observable<any[]> { return this.teams.listTeams(baseUrl, token); }
  listBlueprints(baseUrl: string, token?: string): Observable<any[]> { return this.teams.listBlueprints(baseUrl, token); }
  getBlueprint(baseUrl: string, id: string, token?: string): Observable<any> { return this.teams.getBlueprint(baseUrl, id, token); }
  createBlueprint(baseUrl: string, body: any, token?: string): Observable<any> { return this.teams.createBlueprint(baseUrl, body, token); }
  patchBlueprint(baseUrl: string, id: string, patch: any, token?: string): Observable<any> { return this.teams.patchBlueprint(baseUrl, id, patch, token); }
  deleteBlueprint(baseUrl: string, id: string, token?: string): Observable<any> { return this.teams.deleteBlueprint(baseUrl, id, token); }
  instantiateBlueprint(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.teams.instantiateBlueprint(baseUrl, id, body, token); }
  listTeamTypes(baseUrl: string, token?: string): Observable<any[]> { return this.teams.listTeamTypes(baseUrl, token); }
  listTeamRoles(baseUrl: string, token?: string): Observable<any[]> { return this.teams.listTeamRoles(baseUrl, token); }
  listRolesForTeamType(baseUrl: string, typeId: string, token?: string): Observable<any[]> { return this.teams.listRolesForTeamType(baseUrl, typeId, token); }
  createTeamType(baseUrl: string, body: any, token?: string): Observable<any> { return this.teams.createTeamType(baseUrl, body, token); }
  createRole(baseUrl: string, body: any, token?: string): Observable<any> { return this.teams.createRole(baseUrl, body, token); }
  linkRoleToType(baseUrl: string, typeId: string, roleId: string, token?: string): Observable<any> { return this.teams.linkRoleToType(baseUrl, typeId, roleId, token); }
  updateRoleTemplateMapping(baseUrl: string, typeId: string, roleId: string, templateId: string | null, token?: string): Observable<any> { return this.teams.updateRoleTemplateMapping(baseUrl, typeId, roleId, templateId, token); }
  unlinkRoleFromType(baseUrl: string, typeId: string, roleId: string, token?: string): Observable<any> { return this.teams.unlinkRoleFromType(baseUrl, typeId, roleId, token); }
  deleteTeamType(baseUrl: string, id: string, token?: string): Observable<any> { return this.teams.deleteTeamType(baseUrl, id, token); }
  deleteRole(baseUrl: string, id: string, token?: string): Observable<any> { return this.teams.deleteRole(baseUrl, id, token); }
  createTeam(baseUrl: string, body: any, token?: string): Observable<any> { return this.teams.createTeam(baseUrl, body, token); }
  patchTeam(baseUrl: string, id: string, patch: any, token?: string): Observable<any> { return this.teams.patchTeam(baseUrl, id, patch, token); }
  deleteTeam(baseUrl: string, id: string, token?: string): Observable<any> { return this.teams.deleteTeam(baseUrl, id, token); }
  activateTeam(baseUrl: string, id: string, token?: string): Observable<any> { return this.teams.activateTeam(baseUrl, id, token); }
  setupScrumTeam(baseUrl: string, name?: string, token?: string): Observable<any> { return this.teams.setupScrumTeam(baseUrl, name, token); }

  getAutopilotStatus(baseUrl: string, token?: string): Observable<any> { return this.automation.getAutopilotStatus(baseUrl, token); }
  startAutopilot(baseUrl: string, body: { interval_seconds?: number; max_concurrency?: number; goal?: string; team_id?: string; budget_label?: string; security_level?: 'safe' | 'balanced' | 'aggressive'; }, token?: string): Observable<any> { return this.automation.startAutopilot(baseUrl, body, token); }
  stopAutopilot(baseUrl: string, token?: string): Observable<any> { return this.automation.stopAutopilot(baseUrl, token); }
  tickAutopilot(baseUrl: string, token?: string): Observable<any> { return this.automation.tickAutopilot(baseUrl, token); }
  getAutoPlannerStatus(baseUrl: string, token?: string): Observable<any> { return this.automation.getAutoPlannerStatus(baseUrl, token); }
  configureAutoPlanner(baseUrl: string, config: any, token?: string): Observable<any> { return this.automation.configureAutoPlanner(baseUrl, config, token); }
  planGoal(baseUrl: string, body: { goal: string; context?: string; team_id?: string; parent_task_id?: string; create_tasks?: boolean }, token?: string): Observable<any> { return this.automation.planGoal(baseUrl, body, token); }
  analyzeTaskForFollowups(baseUrl: string, taskId: string, body?: { output?: string; exit_code?: number }, token?: string): Observable<any> { return this.automation.analyzeTaskForFollowups(baseUrl, taskId, body, token); }
  getTriggersStatus(baseUrl: string, token?: string): Observable<any> { return this.automation.getTriggersStatus(baseUrl, token); }
  configureTriggers(baseUrl: string, config: any, token?: string): Observable<any> { return this.automation.configureTriggers(baseUrl, config, token); }
  testTrigger(baseUrl: string, body: { source: string; payload: any }, token?: string): Observable<any> { return this.automation.testTrigger(baseUrl, body, token); }
}
