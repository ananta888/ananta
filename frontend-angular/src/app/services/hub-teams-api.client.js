var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { HubApiCoreService } from './hub-api-core.service';
let HubTeamsApiClient = class HubTeamsApiClient {
    constructor() {
        this.core = inject(HubApiCoreService);
    }
    listBlueprints(baseUrl, token) { return this.core.get(`${baseUrl}/teams/blueprints`, baseUrl, token, false); }
    getBlueprint(baseUrl, id, token) { return this.core.get(`${baseUrl}/teams/blueprints/${id}`, baseUrl, token, false); }
    createBlueprint(baseUrl, body, token) { return this.core.post(`${baseUrl}/teams/blueprints`, body, baseUrl, token); }
    patchBlueprint(baseUrl, id, patch, token) { return this.core.patch(`${baseUrl}/teams/blueprints/${id}`, patch, baseUrl, token); }
    deleteBlueprint(baseUrl, id, token) { return this.core.delete(`${baseUrl}/teams/blueprints/${id}`, baseUrl, token); }
    instantiateBlueprint(baseUrl, id, body, token) { return this.core.post(`${baseUrl}/teams/blueprints/${id}/instantiate`, body, baseUrl, token); }
    listTeams(baseUrl, token) { return this.core.get(`${baseUrl}/teams`, baseUrl, token, true); }
    listTeamTypes(baseUrl, token) { return this.core.get(`${baseUrl}/teams/types`, baseUrl, token, false); }
    listTeamRoles(baseUrl, token) { return this.core.get(`${baseUrl}/teams/roles`, baseUrl, token, false); }
    listRolesForTeamType(baseUrl, typeId, token) { return this.core.get(`${baseUrl}/teams/types/${typeId}/roles`, baseUrl, token, false); }
    createTeamType(baseUrl, body, token) { return this.core.post(`${baseUrl}/teams/types`, body, baseUrl, token); }
    createRole(baseUrl, body, token) { return this.core.post(`${baseUrl}/teams/roles`, body, baseUrl, token); }
    linkRoleToType(baseUrl, typeId, roleId, token) { return this.core.post(`${baseUrl}/teams/types/${typeId}/roles`, { role_id: roleId }, baseUrl, token); }
    updateRoleTemplateMapping(baseUrl, typeId, roleId, templateId, token) { return this.core.patch(`${baseUrl}/teams/types/${typeId}/roles/${roleId}`, { template_id: templateId }, baseUrl, token); }
    unlinkRoleFromType(baseUrl, typeId, roleId, token) { return this.core.delete(`${baseUrl}/teams/types/${typeId}/roles/${roleId}`, baseUrl, token); }
    deleteTeamType(baseUrl, id, token) { return this.core.delete(`${baseUrl}/teams/types/${id}`, baseUrl, token); }
    deleteRole(baseUrl, id, token) { return this.core.delete(`${baseUrl}/teams/roles/${id}`, baseUrl, token); }
    createTeam(baseUrl, body, token) { return this.core.post(`${baseUrl}/teams`, body, baseUrl, token); }
    patchTeam(baseUrl, id, patch, token) { return this.core.patch(`${baseUrl}/teams/${id}`, patch, baseUrl, token); }
    deleteTeam(baseUrl, id, token) { return this.core.delete(`${baseUrl}/teams/${id}`, baseUrl, token); }
    activateTeam(baseUrl, id, token) { return this.core.post(`${baseUrl}/teams/${id}/activate`, {}, baseUrl, token); }
    setupScrumTeam(baseUrl, name, token) { return this.core.post(`${baseUrl}/teams/setup-scrum`, { name }, baseUrl, token); }
};
HubTeamsApiClient = __decorate([
    Injectable({ providedIn: 'root' })
], HubTeamsApiClient);
export { HubTeamsApiClient };
//# sourceMappingURL=hub-teams-api.client.js.map