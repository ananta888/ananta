var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { HubTemplatesApiClient } from './hub-templates-api.client';
import { HubConfigApiClient } from './hub-config-api.client';
import { HubTasksApiClient } from './hub-tasks-api.client';
import { HubSystemApiClient } from './hub-system-api.client';
import { HubTeamsApiClient } from './hub-teams-api.client';
import { HubAutomationApiClient } from './hub-automation-api.client';
import { HubArtifactsApiClient } from './hub-artifacts-api.client';
let HubApiService = class HubApiService {
    constructor() {
        this.templates = inject(HubTemplatesApiClient);
        this.config = inject(HubConfigApiClient);
        this.tasks = inject(HubTasksApiClient);
        this.system = inject(HubSystemApiClient);
        this.teams = inject(HubTeamsApiClient);
        this.automation = inject(HubAutomationApiClient);
        this.artifacts = inject(HubArtifactsApiClient);
    }
    listTemplates(baseUrl, token) { return this.templates.listTemplates(baseUrl, token); }
    createTemplate(baseUrl, tpl, token) { return this.templates.createTemplate(baseUrl, tpl, token); }
    updateTemplate(baseUrl, id, patch, token) { return this.templates.updateTemplate(baseUrl, id, patch, token); }
    deleteTemplate(baseUrl, id, token) { return this.templates.deleteTemplate(baseUrl, id, token); }
    getConfig(baseUrl, token) { return this.config.getConfig(baseUrl, token); }
    getAssistantReadModel(baseUrl, token) { return this.config.getAssistantReadModel(baseUrl, token); }
    getDashboardReadModel(baseUrl, optionsOrToken, tokenOrTtlMs, ttlMs) {
        return this.config.getDashboardReadModel(baseUrl, optionsOrToken, tokenOrTtlMs, ttlMs);
    }
    setConfig(baseUrl, cfg, token) { return this.config.setConfig(baseUrl, cfg, token); }
    listProviders(baseUrl, token) { return this.config.listProviders(baseUrl, token); }
    listProviderCatalog(baseUrl, token) { return this.config.listProviderCatalog(baseUrl, token); }
    getLlmBenchmarks(baseUrl, filters, token) { return this.config.getLlmBenchmarks(baseUrl, filters, token); }
    getLlmBenchmarksConfig(baseUrl, token) { return this.config.getLlmBenchmarksConfig(baseUrl, token); }
    listTasks(baseUrl, token) { return this.tasks.listTasks(baseUrl, token); }
    getTask(baseUrl, id, token) { return this.tasks.getTask(baseUrl, id, token); }
    createTask(baseUrl, body, token) { return this.tasks.createTask(baseUrl, body, token); }
    patchTask(baseUrl, id, patch, token) { return this.tasks.patchTask(baseUrl, id, patch, token); }
    assign(baseUrl, id, body, token) { return this.tasks.assign(baseUrl, id, body, token); }
    propose(baseUrl, id, body, token) { return this.tasks.propose(baseUrl, id, body, token); }
    execute(baseUrl, id, body, token) { return this.tasks.execute(baseUrl, id, body, token); }
    getTaskTimeline(baseUrl, filters, token) { return this.tasks.getTaskTimeline(baseUrl, filters, token); }
    getTaskOrchestrationReadModel(baseUrl, token) { return this.tasks.getTaskOrchestrationReadModel(baseUrl, token); }
    ingestOrchestrationTask(baseUrl, body, token) { return this.tasks.ingestOrchestrationTask(baseUrl, body, token); }
    claimOrchestrationTask(baseUrl, body, token) { return this.tasks.claimOrchestrationTask(baseUrl, body, token); }
    completeOrchestrationTask(baseUrl, body, token) { return this.tasks.completeOrchestrationTask(baseUrl, body, token); }
    listGoals(baseUrl, token) { return this.tasks.listGoals(baseUrl, token); }
    getGoal(baseUrl, id, token) { return this.tasks.getGoal(baseUrl, id, token); }
    getGoalDetail(baseUrl, id, token) { return this.tasks.getGoalDetail(baseUrl, id, token); }
    getGoalPlan(baseUrl, id, token) { return this.tasks.getGoalPlan(baseUrl, id, token); }
    patchGoalPlanNode(baseUrl, goalId, nodeId, patch, token) {
        return this.tasks.patchGoalPlanNode(baseUrl, goalId, nodeId, patch, token);
    }
    getGoalGovernanceSummary(baseUrl, goalId, token) { return this.tasks.getGoalGovernanceSummary(baseUrl, goalId, token); }
    createGoal(baseUrl, body, token) { return this.tasks.createGoal(baseUrl, body, token); }
    taskLogs(baseUrl, id, token) { return this.tasks.taskLogs(baseUrl, id, token); }
    streamTaskLogs(baseUrl, id, token) { return this.tasks.streamTaskLogs(baseUrl, id, token); }
    listArchivedTasks(baseUrl, token, limit = 100, offset = 0) {
        return this.tasks.listArchivedTasks(baseUrl, token, limit, offset);
    }
    archiveTask(baseUrl, id, token) { return this.tasks.archiveTask(baseUrl, id, token); }
    restoreTask(baseUrl, id, token) { return this.tasks.restoreTask(baseUrl, id, token); }
    cleanupTasks(baseUrl, body, token) { return this.tasks.cleanupTasks(baseUrl, body, token); }
    cleanupArchivedTasks(baseUrl, body, token) { return this.tasks.cleanupArchivedTasks(baseUrl, body, token); }
    deleteArchivedTask(baseUrl, id, token) { return this.tasks.deleteArchivedTask(baseUrl, id, token); }
    reviewTaskProposal(baseUrl, id, body, token) { return this.tasks.reviewTaskProposal(baseUrl, id, body, token); }
    listArtifacts(baseUrl, token) { return this.artifacts.listArtifacts(baseUrl, token); }
    getArtifact(baseUrl, artifactId, token) { return this.artifacts.getArtifact(baseUrl, artifactId, token); }
    uploadArtifact(baseUrl, file, collectionName, token) {
        return this.artifacts.uploadArtifact(baseUrl, file, collectionName, token);
    }
    extractArtifact(baseUrl, artifactId, token) { return this.artifacts.extractArtifact(baseUrl, artifactId, token); }
    streamSystemEvents(baseUrl, token) { return this.system.streamSystemEvents(baseUrl, token); }
    listAgents(baseUrl, token) { return this.system.listAgents(baseUrl, token); }
    getStats(baseUrl, token) { return this.system.getStats(baseUrl, token); }
    getStatsHistory(baseUrl, token) { return this.system.getStatsHistory(baseUrl, token); }
    getAuditLogs(baseUrl, limit = 100, offset = 0, token) { return this.system.getAuditLogs(baseUrl, limit, offset, token); }
    analyzeAuditLogs(baseUrl, limit = 50, token) { return this.system.analyzeAuditLogs(baseUrl, limit, token); }
    listTeams(baseUrl, token) { return this.teams.listTeams(baseUrl, token); }
    listBlueprints(baseUrl, token) { return this.teams.listBlueprints(baseUrl, token); }
    getBlueprint(baseUrl, id, token) { return this.teams.getBlueprint(baseUrl, id, token); }
    createBlueprint(baseUrl, body, token) { return this.teams.createBlueprint(baseUrl, body, token); }
    patchBlueprint(baseUrl, id, patch, token) { return this.teams.patchBlueprint(baseUrl, id, patch, token); }
    deleteBlueprint(baseUrl, id, token) { return this.teams.deleteBlueprint(baseUrl, id, token); }
    instantiateBlueprint(baseUrl, id, body, token) { return this.teams.instantiateBlueprint(baseUrl, id, body, token); }
    listTeamTypes(baseUrl, token) { return this.teams.listTeamTypes(baseUrl, token); }
    listTeamRoles(baseUrl, token) { return this.teams.listTeamRoles(baseUrl, token); }
    listRolesForTeamType(baseUrl, typeId, token) { return this.teams.listRolesForTeamType(baseUrl, typeId, token); }
    createTeamType(baseUrl, body, token) { return this.teams.createTeamType(baseUrl, body, token); }
    createRole(baseUrl, body, token) { return this.teams.createRole(baseUrl, body, token); }
    linkRoleToType(baseUrl, typeId, roleId, token) { return this.teams.linkRoleToType(baseUrl, typeId, roleId, token); }
    updateRoleTemplateMapping(baseUrl, typeId, roleId, templateId, token) { return this.teams.updateRoleTemplateMapping(baseUrl, typeId, roleId, templateId, token); }
    unlinkRoleFromType(baseUrl, typeId, roleId, token) { return this.teams.unlinkRoleFromType(baseUrl, typeId, roleId, token); }
    deleteTeamType(baseUrl, id, token) { return this.teams.deleteTeamType(baseUrl, id, token); }
    deleteRole(baseUrl, id, token) { return this.teams.deleteRole(baseUrl, id, token); }
    createTeam(baseUrl, body, token) { return this.teams.createTeam(baseUrl, body, token); }
    patchTeam(baseUrl, id, patch, token) { return this.teams.patchTeam(baseUrl, id, patch, token); }
    deleteTeam(baseUrl, id, token) { return this.teams.deleteTeam(baseUrl, id, token); }
    activateTeam(baseUrl, id, token) { return this.teams.activateTeam(baseUrl, id, token); }
    setupScrumTeam(baseUrl, name, token) { return this.teams.setupScrumTeam(baseUrl, name, token); }
    getAutopilotStatus(baseUrl, token) { return this.automation.getAutopilotStatus(baseUrl, token); }
    startAutopilot(baseUrl, body, token) { return this.automation.startAutopilot(baseUrl, body, token); }
    stopAutopilot(baseUrl, token) { return this.automation.stopAutopilot(baseUrl, token); }
    tickAutopilot(baseUrl, token) { return this.automation.tickAutopilot(baseUrl, token); }
    getAutoPlannerStatus(baseUrl, token) { return this.automation.getAutoPlannerStatus(baseUrl, token); }
    configureAutoPlanner(baseUrl, config, token) { return this.automation.configureAutoPlanner(baseUrl, config, token); }
    planGoal(baseUrl, body, token) { return this.automation.planGoal(baseUrl, body, token); }
    analyzeTaskForFollowups(baseUrl, taskId, body, token) { return this.automation.analyzeTaskForFollowups(baseUrl, taskId, body, token); }
    getTriggersStatus(baseUrl, token) { return this.automation.getTriggersStatus(baseUrl, token); }
    configureTriggers(baseUrl, config, token) { return this.automation.configureTriggers(baseUrl, config, token); }
    testTrigger(baseUrl, body, token) { return this.automation.testTrigger(baseUrl, body, token); }
};
HubApiService = __decorate([
    Injectable({ providedIn: 'root' })
], HubApiService);
export { HubApiService };
//# sourceMappingURL=hub-api.service.js.map