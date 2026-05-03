import { Injectable, inject } from '@angular/core';

import { HubApiService } from '../../services/hub-api.service';

@Injectable({ providedIn: 'root' })
export class AdminFacade {
  private hubApi = inject(HubApiService);

  getConfig(baseUrl: string, token?: string) { return this.hubApi.getConfig(baseUrl, token); }
  listTemplates(baseUrl: string, token?: string) { return this.hubApi.listTemplates(baseUrl, token); }
  createTemplate(baseUrl: string, body: any, token?: string) { return this.hubApi.createTemplate(baseUrl, body, token); }
  updateTemplate(baseUrl: string, id: string, patch: any, token?: string) { return this.hubApi.updateTemplate(baseUrl, id, patch, token); }
  deleteTemplate(baseUrl: string, id: string, token?: string) { return this.hubApi.deleteTemplate(baseUrl, id, token); }
  getTemplateVariableRegistry(baseUrl: string, token?: string) { return this.hubApi.getTemplateVariableRegistry(baseUrl, token); }
  getTemplateSampleContexts(baseUrl: string, token?: string) { return this.hubApi.getTemplateSampleContexts(baseUrl, token); }
  validateTemplate(baseUrl: string, body: any, token?: string) { return this.hubApi.validateTemplate(baseUrl, body, token); }
  previewTemplate(baseUrl: string, body: any, token?: string) { return this.hubApi.previewTemplate(baseUrl, body, token); }
  templateValidationDiagnostics(baseUrl: string, body: any, token?: string) {
    return this.hubApi.templateValidationDiagnostics(baseUrl, body, token);
  }

  listArtifacts(baseUrl: string, token?: string) { return this.hubApi.listArtifacts(baseUrl, token); }
  getArtifact(baseUrl: string, artifactId: string, token?: string) { return this.hubApi.getArtifact(baseUrl, artifactId, token); }
  uploadArtifact(baseUrl: string, file: File, collectionName?: string, token?: string) { return this.hubApi.uploadArtifact(baseUrl, file, collectionName, token); }
  extractArtifact(baseUrl: string, artifactId: string, token?: string) { return this.hubApi.extractArtifact(baseUrl, artifactId, token); }
  indexArtifact(baseUrl: string, artifactId: string, body?: any, token?: string) { return this.hubApi.indexArtifact(baseUrl, artifactId, body, token); }
  getArtifactRagStatus(baseUrl: string, artifactId: string, token?: string) { return this.hubApi.getArtifactRagStatus(baseUrl, artifactId, token); }
  getArtifactRagPreview(baseUrl: string, artifactId: string, limit = 5, token?: string) { return this.hubApi.getArtifactRagPreview(baseUrl, artifactId, limit, token); }
  listKnowledgeCollections(baseUrl: string, token?: string) { return this.hubApi.listKnowledgeCollections(baseUrl, token); }
  listKnowledgeIndexProfiles(baseUrl: string, token?: string) { return this.hubApi.listKnowledgeIndexProfiles(baseUrl, token); }
  listWikiPresets(baseUrl: string, token?: string) { return this.hubApi.listWikiPresets(baseUrl, token); }
  createKnowledgeCollection(baseUrl: string, payload: { name: string; description?: string }, token?: string) { return this.hubApi.createKnowledgeCollection(baseUrl, payload, token); }
  getKnowledgeCollection(baseUrl: string, collectionId: string, token?: string) { return this.hubApi.getKnowledgeCollection(baseUrl, collectionId, token); }
  indexKnowledgeCollection(baseUrl: string, collectionId: string, body?: any, token?: string) { return this.hubApi.indexKnowledgeCollection(baseUrl, collectionId, body, token); }
  searchKnowledgeCollection(baseUrl: string, collectionId: string, payload: { query: string; top_k?: number }, token?: string) { return this.hubApi.searchKnowledgeCollection(baseUrl, collectionId, payload, token); }
  importWikiFromUrl(baseUrl: string, payload: any, token?: string) { return this.hubApi.importWikiFromUrl(baseUrl, payload, token); }
  getWikiImportJob(baseUrl: string, jobId: string, token?: string) { return this.hubApi.getWikiImportJob(baseUrl, jobId, token); }

  listTeams(baseUrl: string, token?: string) { return this.hubApi.listTeams(baseUrl, token); }
  getTaskOrchestrationReadModel(baseUrl: string, token?: string) { return this.hubApi.getTaskOrchestrationReadModel(baseUrl, token); }
  listTeamTypes(baseUrl: string, token?: string) { return this.hubApi.listTeamTypes(baseUrl, token); }
  listTeamRoles(baseUrl: string, token?: string) { return this.hubApi.listTeamRoles(baseUrl, token); }
  listAgents(baseUrl: string, token?: string) { return this.hubApi.listAgents(baseUrl, token); }
  listBlueprints(baseUrl: string, token?: string) { return this.hubApi.listBlueprints(baseUrl, token); }
  listBlueprintCatalog(baseUrl: string, token?: string) { return this.hubApi.listBlueprintCatalog(baseUrl, token); }
  createBlueprint(baseUrl: string, body: any, token?: string) { return this.hubApi.createBlueprint(baseUrl, body, token); }
  patchBlueprint(baseUrl: string, id: string, patch: any, token?: string) { return this.hubApi.patchBlueprint(baseUrl, id, patch, token); }
  deleteBlueprint(baseUrl: string, id: string, token?: string) { return this.hubApi.deleteBlueprint(baseUrl, id, token); }
  instantiateBlueprint(baseUrl: string, id: string, body: any, token?: string) { return this.hubApi.instantiateBlueprint(baseUrl, id, body, token); }
  createTeamType(baseUrl: string, body: any, token?: string) { return this.hubApi.createTeamType(baseUrl, body, token); }
  deleteTeamType(baseUrl: string, id: string, token?: string) { return this.hubApi.deleteTeamType(baseUrl, id, token); }
  createRole(baseUrl: string, body: any, token?: string) { return this.hubApi.createRole(baseUrl, body, token); }
  deleteRole(baseUrl: string, id: string, token?: string) { return this.hubApi.deleteRole(baseUrl, id, token); }
  linkRoleToType(baseUrl: string, typeId: string, roleId: string, token?: string) { return this.hubApi.linkRoleToType(baseUrl, typeId, roleId, token); }
  unlinkRoleFromType(baseUrl: string, typeId: string, roleId: string, token?: string) { return this.hubApi.unlinkRoleFromType(baseUrl, typeId, roleId, token); }
  updateRoleTemplateMapping(baseUrl: string, typeId: string, roleId: string, templateId: string | null, token?: string) {
    return this.hubApi.updateRoleTemplateMapping(baseUrl, typeId, roleId, templateId, token);
  }
  createTeam(baseUrl: string, body: any, token?: string) { return this.hubApi.createTeam(baseUrl, body, token); }
  patchTeam(baseUrl: string, id: string, patch: any, token?: string) { return this.hubApi.patchTeam(baseUrl, id, patch, token); }
  activateTeam(baseUrl: string, id: string, token?: string) { return this.hubApi.activateTeam(baseUrl, id, token); }
  deleteTeam(baseUrl: string, id: string, token?: string) { return this.hubApi.deleteTeam(baseUrl, id, token); }
}
