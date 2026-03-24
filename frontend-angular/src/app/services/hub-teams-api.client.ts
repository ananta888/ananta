import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubTeamsApiClient {
  private core = inject(HubApiCoreService);
  listBlueprints(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/teams/blueprints`, baseUrl, token, false); }
  getBlueprint(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.get<any>(`${baseUrl}/teams/blueprints/${id}`, baseUrl, token, false); }
  createBlueprint(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/teams/blueprints`, body, baseUrl, token); }
  patchBlueprint(baseUrl: string, id: string, patch: any, token?: string): Observable<any> { return this.core.patch(`${baseUrl}/teams/blueprints/${id}`, patch, baseUrl, token); }
  deleteBlueprint(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.delete(`${baseUrl}/teams/blueprints/${id}`, baseUrl, token); }
  instantiateBlueprint(baseUrl: string, id: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/teams/blueprints/${id}/instantiate`, body, baseUrl, token); }
  listTeams(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/teams`, baseUrl, token, true); }
  listTeamTypes(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/teams/types`, baseUrl, token, false); }
  listTeamRoles(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/teams/roles`, baseUrl, token, false); }
  listRolesForTeamType(baseUrl: string, typeId: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/teams/types/${typeId}/roles`, baseUrl, token, false); }
  createTeamType(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/teams/types`, body, baseUrl, token); }
  createRole(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/teams/roles`, body, baseUrl, token); }
  linkRoleToType(baseUrl: string, typeId: string, roleId: string, token?: string): Observable<any> { return this.core.post(`${baseUrl}/teams/types/${typeId}/roles`, { role_id: roleId }, baseUrl, token); }
  updateRoleTemplateMapping(baseUrl: string, typeId: string, roleId: string, templateId: string | null, token?: string): Observable<any> { return this.core.patch(`${baseUrl}/teams/types/${typeId}/roles/${roleId}`, { template_id: templateId }, baseUrl, token); }
  unlinkRoleFromType(baseUrl: string, typeId: string, roleId: string, token?: string): Observable<any> { return this.core.delete(`${baseUrl}/teams/types/${typeId}/roles/${roleId}`, baseUrl, token); }
  deleteTeamType(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.delete(`${baseUrl}/teams/types/${id}`, baseUrl, token); }
  deleteRole(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.delete(`${baseUrl}/teams/roles/${id}`, baseUrl, token); }
  createTeam(baseUrl: string, body: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/teams`, body, baseUrl, token); }
  patchTeam(baseUrl: string, id: string, patch: any, token?: string): Observable<any> { return this.core.patch(`${baseUrl}/teams/${id}`, patch, baseUrl, token); }
  deleteTeam(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.delete(`${baseUrl}/teams/${id}`, baseUrl, token); }
  activateTeam(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.post(`${baseUrl}/teams/${id}/activate`, {}, baseUrl, token); }
  setupScrumTeam(baseUrl: string, name?: string, token?: string): Observable<any> { return this.core.post(`${baseUrl}/teams/setup-scrum`, { name }, baseUrl, token); }
}
