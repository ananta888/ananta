import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { UiSkeletonComponent } from './ui-skeleton.component';
import { AdminFacade } from '../features/admin/admin.facade';

type BlueprintRoleForm = {
  id?: string;
  name: string;
  description: string;
  template_id: string;
  sort_order: number;
  is_required: boolean;
  config: any;
};

type BlueprintArtifactForm = {
  id?: string;
  kind: string;
  title: string;
  description: string;
  sort_order: number;
  payload: any;
};

@Component({
  standalone: true,
  selector: 'app-teams',
  imports: [FormsModule, UiSkeletonComponent],
  template: `
    <div class="teams-shell">
      <div class="teams-hero">
        <div>
          <div class="teams-kicker">Blueprint-first Teams</div>
          <h2 class="teams-title">Teams werden ueber Blueprints erstellt und danach gezielt verfeinert.</h2>
          <p class="teams-copy">
            Rollen, Start-Artefakte und Basistypen leben im Blueprint. Low-level Pflege bleibt im Advanced-Modus erhalten.
          </p>
        </div>
      <div class="teams-hero-actions">
        <button class="btn-primary" (click)="currentTab = 'blueprints'">Blueprints</button>
        <button class="btn-secondary" (click)="currentTab = 'teams'">Team erstellen</button>
        <button class="btn-secondary" (click)="refresh()">Aktualisieren</button>
      </div>
      </div>

      <div class="tabs teams-tabs">
        <button type="button" class="tab" [class.active]="currentTab === 'blueprints'" (click)="currentTab = 'blueprints'">Blueprints</button>
        <button type="button" class="tab" [class.active]="currentTab === 'teams'" (click)="currentTab = 'teams'">Teams aus Blueprint</button>
        <button type="button" class="tab" [class.active]="currentTab === 'advanced'" (click)="currentTab = 'advanced'">Advanced</button>
      </div>

      @if (loading) {
        <div class="card">
          <app-ui-skeleton [count]="2" [columns]="2" [lineCount]="5"></app-ui-skeleton>
        </div>
      }

      @if (currentTab === 'blueprints') {
        <div class="grid cols-2 teams-blueprint-grid">
          <div class="card card-primary teams-list-panel">
            <div class="row flex-between">
              <h3 class="no-margin">Blueprints</h3>
              <button class="btn-secondary btn-small" (click)="startNewBlueprint()" [disabled]="!isAdmin">Neu</button>
            </div>
            <p class="muted no-margin">Seed-Blueprints fuer Scrum und Kanban sind automatisch vorhanden.</p>

            <div class="teams-blueprint-list">
              @for (blueprint of blueprints; track blueprint.id) {
                <button type="button" class="teams-blueprint-card" [class.selected]="selectedBlueprintId === blueprint.id" (click)="selectBlueprint(blueprint)">
                  <div class="row flex-between">
                    <strong>{{ blueprint.name }}</strong>
                    @if (blueprint.is_seed) {
                      <span class="teams-pill teams-pill-seed">Seed</span>
                    }
                  </div>
                  <div class="muted teams-blueprint-meta">
                    {{ blueprint.base_team_type_name || 'Kein Basis-Typ' }} · {{ blueprint.roles?.length || 0 }} Rollen · {{ blueprint.artifacts?.length || 0 }} Artefakte
                  </div>
                  <p class="muted teams-blueprint-desc">{{ blueprint.description || 'Keine Beschreibung' }}</p>
                </button>
              }
            </div>
          </div>

          <div class="card teams-editor-panel">
            <div class="row flex-between">
              <div>
                <h3 class="no-margin">{{ blueprintForm.id ? 'Blueprint bearbeiten' : 'Neuen Blueprint anlegen' }}</h3>
                <div class="muted">Master-Detail Editor fuer Rollen, Templates und Artefakte.</div>
              </div>
              @if (isSelectedSeedBlueprint()) {
                <span class="teams-pill teams-pill-seed">Seed</span>
              }
            </div>

            @if (!isAdmin) {
              <div class="state-banner warning">Blueprints koennen nur von Admins geaendert werden.</div>
            }

            <div class="grid cols-2">
              <label>Name <input [(ngModel)]="blueprintForm.name" [disabled]="!isAdmin || isSelectedSeedBlueprint()"></label>
              <label>Basis-Team-Typ
                <select [(ngModel)]="blueprintForm.base_team_type_name" [disabled]="!isAdmin">
                  <option value="">-- Kein Basis-Typ --</option>
                  @for (type of teamTypesList; track type.id) {
                    <option [value]="type.name">{{ type.name }}</option>
                  }
                </select>
              </label>
            </div>
            <label>Beschreibung <textarea [(ngModel)]="blueprintForm.description" rows="3" [disabled]="!isAdmin"></textarea></label>

            <div class="teams-section">
              <div class="row flex-between">
                <h4 class="no-margin">Rollen</h4>
                <button class="btn-secondary btn-small" (click)="addBlueprintRole()" [disabled]="!isAdmin">Rolle hinzufuegen</button>
              </div>
              @for (role of blueprintForm.roles; track role; let i = $index) {
                <div class="teams-inline-card">
                  <div class="grid cols-2">
                    <label>Rollenname <input [(ngModel)]="role.name" [disabled]="!isAdmin"></label>
                    <label>Template
                      <select [(ngModel)]="role.template_id" [disabled]="!isAdmin">
                        <option value="">-- Kein Template --</option>
                        @for (template of templates; track template.id) {
                          <option [value]="template.id">{{ template.name }}</option>
                        }
                      </select>
                    </label>
                    <label>Beschreibung <input [(ngModel)]="role.description" [disabled]="!isAdmin"></label>
                    <label>Sortierung <input type="number" [(ngModel)]="role.sort_order" [disabled]="!isAdmin"></label>
                  </div>
                  <div class="row flex-between">
                    <label class="teams-checkbox"><input type="checkbox" [(ngModel)]="role.is_required" [disabled]="!isAdmin"> Pflichtrolle</label>
                    <button class="danger btn-sm-action" (click)="removeBlueprintRole(i)" [disabled]="!isAdmin">Entfernen</button>
                  </div>
                </div>
              }
            </div>

            <div class="teams-section">
              <div class="row flex-between">
                <h4 class="no-margin">Artefakte</h4>
                <button class="btn-secondary btn-small" (click)="addBlueprintArtifact()" [disabled]="!isAdmin">Artefakt hinzufuegen</button>
              </div>
              @for (artifact of blueprintForm.artifacts; track artifact; let i = $index) {
                <div class="teams-inline-card">
                  <div class="grid cols-2">
                    <label>Typ
                      <select [(ngModel)]="artifact.kind" [disabled]="!isAdmin">
                        <option value="task">task</option>
                      </select>
                    </label>
                    <label>Sortierung <input type="number" [(ngModel)]="artifact.sort_order" [disabled]="!isAdmin"></label>
                    <label>Titel <input [(ngModel)]="artifact.title" [disabled]="!isAdmin"></label>
                    <label>Status
                      <select [(ngModel)]="artifact.payload.status" [disabled]="!isAdmin">
                        <option value="backlog">backlog</option>
                        <option value="todo">todo</option>
                        <option value="in_progress">in_progress</option>
                      </select>
                    </label>
                    <label class="col-span-full">Beschreibung <textarea [(ngModel)]="artifact.description" rows="2" [disabled]="!isAdmin"></textarea></label>
                    <label>Prioritaet
                      <select [(ngModel)]="artifact.payload.priority" [disabled]="!isAdmin">
                        <option value="High">High</option>
                        <option value="Medium">Medium</option>
                        <option value="Low">Low</option>
                      </select>
                    </label>
                  </div>
                  <div class="row flex-end">
                    <button class="danger btn-sm-action" (click)="removeBlueprintArtifact(i)" [disabled]="!isAdmin">Entfernen</button>
                  </div>
                </div>
              }
            </div>

            <div class="row">
              <button class="btn-primary" (click)="saveBlueprint()" [disabled]="busy || !isAdmin || !blueprintForm.name.trim()">{{ blueprintForm.id ? 'Speichern' : 'Erstellen' }}</button>
              <button class="btn-secondary" (click)="startNewBlueprint()">Zuruecksetzen</button>
              <button class="btn-secondary" (click)="prepareInstantiateFromEditor()" [disabled]="!blueprintForm.id">Fuer Team-Erstellung uebernehmen</button>
              @if (blueprintForm.id && !isSelectedSeedBlueprint()) {
                <button class="danger" (click)="deleteBlueprint(blueprintForm.id)" [disabled]="busy || !isAdmin">Loeschen</button>
              }
            </div>
          </div>
        </div>
      }

      @if (currentTab === 'teams') {
        <div class="grid cols-2 teams-blueprint-grid">
          <div class="card card-success">
            <h3 class="no-margin">Team aus Blueprint erstellen</h3>
            <p class="muted">Blueprint waehlen, Team benennen, optional Agenten den Blueprint-Rollen zuweisen.</p>

            <div class="grid cols-2">
              <label>Blueprint
                <select [(ngModel)]="teamFromBlueprint.blueprint_id" (ngModelChange)="onInstantiateBlueprintChange($event)" [disabled]="!isAdmin">
                  <option value="">-- Blueprint waehlen --</option>
                  @for (blueprint of blueprints; track blueprint.id) {
                    <option [value]="blueprint.id">{{ blueprint.name }}</option>
                  }
                </select>
              </label>
              <label>Teamname <input [(ngModel)]="teamFromBlueprint.name" [disabled]="!isAdmin"></label>
              <label class="col-span-full">Beschreibung / Override <textarea [(ngModel)]="teamFromBlueprint.description" rows="2" [disabled]="!isAdmin"></textarea></label>
            </div>
            <label class="teams-checkbox"><input type="checkbox" [(ngModel)]="teamFromBlueprint.activate" [disabled]="!isAdmin"> Team direkt aktivieren</label>

            @if (selectedInstantiateBlueprint) {
              <div class="teams-summary-card">
                <div class="row flex-between">
                  <strong>{{ selectedInstantiateBlueprint.name }}</strong>
                  @if (selectedInstantiateBlueprint.is_seed) {
                    <span class="teams-pill teams-pill-seed">Seed</span>
                  }
                </div>
                <div class="muted">{{ selectedInstantiateBlueprint.description || 'Keine Beschreibung' }}</div>
                <div class="teams-summary-meta">{{ selectedInstantiateBlueprint.base_team_type_name || 'Kein Basis-Typ' }} · {{ selectedInstantiateBlueprint.roles?.length || 0 }} Rollen · {{ selectedInstantiateBlueprint.artifacts?.length || 0 }} Artefakte</div>
              </div>

              <div class="teams-section">
                <div class="row flex-between">
                  <h4 class="no-margin">Mitglieder und Overrides</h4>
                  <button class="btn-secondary btn-small" (click)="addInstantiateMember()" [disabled]="!isAdmin">Mitglied hinzufuegen</button>
                </div>
                @for (member of teamFromBlueprint.members; track member; let i = $index) {
                  <div class="teams-inline-card">
                    <div class="grid cols-3">
                      <label>Blueprint-Rolle
                        <select [(ngModel)]="member.blueprint_role_id" [disabled]="!isAdmin">
                          <option value="">-- Rolle waehlen --</option>
                          @for (role of selectedInstantiateBlueprint.roles; track role.id) {
                            <option [value]="role.id">{{ role.name }}</option>
                          }
                        </select>
                      </label>
                      <label>Agent
                        <select [(ngModel)]="member.agent_url" [disabled]="!isAdmin">
                          <option value="">-- Agent waehlen --</option>
                          @for (agent of availableBlueprintAgents(); track agent.url) {
                            <option [value]="agent.url">{{ agent.name }} ({{ agent.url }})</option>
                          }
                        </select>
                      </label>
                      <label>Custom Template
                        <select [(ngModel)]="member.custom_template_id" [disabled]="!isAdmin">
                          <option value="">-- Standard --</option>
                          @for (template of templates; track template.id) {
                            <option [value]="template.id">{{ template.name }}</option>
                          }
                        </select>
                      </label>
                    </div>
                    <div class="row flex-end">
                      <button class="danger btn-sm-action" (click)="removeInstantiateMember(i)" [disabled]="!isAdmin">Entfernen</button>
                    </div>
                  </div>
                }
              </div>
            }

            <div class="row">
              <button class="btn-primary" (click)="instantiateBlueprint()" [disabled]="busy || !isAdmin || !teamFromBlueprint.blueprint_id || !teamFromBlueprint.name.trim()">Team erstellen</button>
              <button class="btn-secondary" (click)="useSeedBlueprint('Scrum')">Scrum Seed</button>
              <button class="btn-secondary" (click)="useSeedBlueprint('Kanban')">Kanban Seed</button>
            </div>
          </div>

          <div class="card teams-list-panel">
            <div class="row flex-between">
              <h3 class="no-margin">Bestehende Teams</h3>
              <span class="muted">{{ teams.length }} Teams</span>
            </div>
            <div class="grid">
              @for (team of teams; track team.id) {
                <div class="teams-team-card" [class.active-team]="team.is_active">
                  <div class="row flex-between">
                    <div>
                      <strong>{{ team.name }}</strong>
                      @if (team.is_active) {
                        <span class="teams-pill teams-pill-active">Aktiv</span>
                      }
                    </div>
                    <div class="row">
                      <button class="btn-secondary btn-sm-action" (click)="prepareTeamEdit(team)" [disabled]="!isAdmin">Bearbeiten</button>
                      @if (!team.is_active) {
                        <button class="btn-secondary btn-sm-action" (click)="activate(team.id)" [disabled]="!isAdmin">Aktivieren</button>
                      }
                      <button class="danger btn-sm-action" (click)="deleteTeam(team.id)" [disabled]="!isAdmin">Loeschen</button>
                    </div>
                  </div>
                  <div class="muted teams-team-meta">{{ team.blueprint_snapshot?.name || getBlueprintName(team.blueprint_id) || 'Manuell' }} · {{ getTeamTypeName(team.team_type_id) }}</div>
                  <p class="muted teams-blueprint-desc">{{ team.description || 'Keine Beschreibung' }}</p>
                  <div class="teams-member-list">
                    @for (member of team.members || []; track member.id || member.agent_url) {
                      <div class="agent-chip agent-chip-col">
                        <strong>{{ getAgentNameByUrl(member.agent_url) }}</strong>
                        <div class="agent-chip-info">
                          <span class="badge badge-blue">{{ getRoleName(member.role_id) }}</span>
                          @if (member.blueprint_role_id) {
                            <span class="badge badge-gray">{{ getBlueprintRoleName(team, member.blueprint_role_id) }}</span>
                          }
                        </div>
                      </div>
                    }
                  </div>
                </div>
              }
            </div>
          </div>
        </div>
      }

      @if (currentTab === 'advanced') {
        <div class="card teams-advanced-panel">
          <div class="row flex-between">
            <div>
              <h3 class="no-margin">Advanced-Modus</h3>
              <div class="muted">Low-level Pflege fuer Teams, Team-Typen und Rollen. Blueprint-first bleibt der Standard.</div>
            </div>
          </div>
          <div class="tabs teams-subtabs">
            <button type="button" class="tab" [class.active]="advancedTab === 'teams'" (click)="advancedTab = 'teams'">Teams</button>
            <button type="button" class="tab" [class.active]="advancedTab === 'types'" (click)="advancedTab = 'types'">Team-Typen</button>
            <button type="button" class="tab" [class.active]="advancedTab === 'roles'" (click)="advancedTab = 'roles'">Rollen</button>
          </div>

          @if (advancedTab === 'teams') {
            <div class="grid cols-2">
              <div class="card">
                <h4 class="no-margin">Manuelles Team</h4>
                <div class="muted mb-md">Nur verwenden, wenn der Blueprint-Flow bewusst umgangen werden soll.</div>
                <div class="grid cols-2">
                  <label>Name <input [(ngModel)]="newTeam.name" [disabled]="!isAdmin"></label>
                  <label>Typ
                    <select [(ngModel)]="newTeam.team_type_id" [disabled]="!isAdmin">
                      <option value="">-- Typ waehlen --</option>
                      @for (type of teamTypesList; track type.id) {
                        <option [value]="type.id">{{ type.name }}</option>
                      }
                    </select>
                  </label>
                  <label class="col-span-full">Beschreibung <textarea [(ngModel)]="newTeam.description" rows="2" [disabled]="!isAdmin"></textarea></label>
                </div>
                <div class="teams-section">
                  <div class="row flex-between">
                    <h4 class="no-margin">Mitglieder</h4>
                    <button class="btn-secondary btn-small" (click)="addManualMember()" [disabled]="!isAdmin">Mitglied hinzufuegen</button>
                  </div>
                  @for (member of newTeam.members; track member; let i = $index) {
                    <div class="teams-inline-card">
                      <div class="grid cols-3">
                        <label>Agent
                          <select [(ngModel)]="member.agent_url" [disabled]="!isAdmin">
                            <option value="">-- Agent waehlen --</option>
                            @for (agent of availableAgents(newTeam); track agent.url) {
                              <option [value]="agent.url">{{ agent.name }}</option>
                            }
                          </select>
                        </label>
                        <label>Rolle
                          <select [(ngModel)]="member.role_id" [disabled]="!isAdmin">
                            <option value="">-- Rolle waehlen --</option>
                            @for (role of getRolesForType(newTeam.team_type_id); track role.id) {
                              <option [value]="role.id">{{ role.name }}</option>
                            }
                          </select>
                        </label>
                        <label>Custom Template
                          <select [(ngModel)]="member.custom_template_id" [disabled]="!isAdmin">
                            <option value="">-- Standard --</option>
                            @for (template of templates; track template.id) {
                              <option [value]="template.id">{{ template.name }}</option>
                            }
                          </select>
                        </label>
                      </div>
                      <div class="row flex-end">
                        <button class="danger btn-sm-action" (click)="removeMemberFromForm(i)" [disabled]="!isAdmin">Entfernen</button>
                      </div>
                    </div>
                  }
                </div>
                <div class="row">
                  <button class="btn-primary" (click)="createTeam()" [disabled]="busy || !isAdmin || !newTeam.name.trim()">Speichern</button>
                  <button class="btn-secondary" (click)="resetForm()">Zuruecksetzen</button>
                </div>
              </div>

              <div class="card">
                <h4 class="no-margin">Vorhandene Teams</h4>
                <div class="grid mt-md">
                  @for (team of teams; track team.id) {
                    <div class="teams-inline-card">
                      <div class="row flex-between">
                        <div>
                          <strong>{{ team.name }}</strong>
                          <div class="muted">{{ team.blueprint_snapshot?.name || 'Manuell' }} · {{ getTeamTypeName(team.team_type_id) }}</div>
                        </div>
                        <button class="btn-secondary btn-sm-action" (click)="prepareTeamEdit(team)" [disabled]="!isAdmin">Ins Formular</button>
                      </div>
                    </div>
                  }
                </div>
              </div>
            </div>
          }

          @if (advancedTab === 'types') {
            <div class="grid cols-2">
              <div class="card">
                <h4 class="no-margin">Team-Typ erstellen</h4>
                <div class="grid cols-2 mt-md">
                  <label>Name <input [(ngModel)]="newType.name" [disabled]="!isAdmin"></label>
                  <label>Beschreibung <input [(ngModel)]="newType.description" [disabled]="!isAdmin"></label>
                </div>
                <div class="row mt-md">
                  <button class="btn-primary" (click)="createTeamType()" [disabled]="busy || !isAdmin || !newType.name.trim()">Typ erstellen</button>
                </div>
              </div>

              <div class="grid">
                @for (type of teamTypesList; track type.id) {
                  <div class="card">
                    <div class="row flex-between">
                      <strong>{{ type.name }}</strong>
                      <button class="danger btn-sm-action" (click)="deleteTeamType(type.id)" [disabled]="!isAdmin">Loeschen</button>
                    </div>
                    <div class="muted mb-md">{{ type.description }}</div>
                    <div class="row wrap">
                      @for (role of allRoles; track role.id) {
                        <label class="teams-role-toggle">
                          <input type="checkbox" [checked]="isRoleLinked(type, role.id)" (change)="toggleRoleForType(type.id, role.id, isRoleLinked(type, role.id))" [disabled]="!isAdmin">
                          <span>{{ role.name }}</span>
                        </label>
                        @if (isRoleLinked(type, role.id)) {
                          <select class="select-sm" [ngModel]="getRoleTemplateMapping(type.id, role.id)" (ngModelChange)="setRoleTemplateMapping(type.id, role.id, $event)" [disabled]="!isAdmin">
                            <option value="">-- Template --</option>
                            @for (template of templates; track template.id) {
                              <option [value]="template.id">{{ template.name }}</option>
                            }
                          </select>
                        }
                      }
                    </div>
                  </div>
                }
              </div>
            </div>
          }

          @if (advancedTab === 'roles') {
            <div class="grid cols-2">
              <div class="card">
                <h4 class="no-margin">Rolle erstellen</h4>
                <div class="grid cols-3 mt-md">
                  <label>Name <input [(ngModel)]="newRole.name" [disabled]="!isAdmin"></label>
                  <label>Beschreibung <input [(ngModel)]="newRole.description" [disabled]="!isAdmin"></label>
                  <label>Standard Template
                    <select [(ngModel)]="newRole.default_template_id" [disabled]="!isAdmin">
                      <option value="">-- Kein Template --</option>
                      @for (template of templates; track template.id) {
                        <option [value]="template.id">{{ template.name }}</option>
                      }
                    </select>
                  </label>
                </div>
                <div class="row mt-md">
                  <button class="btn-primary" (click)="createRole()" [disabled]="busy || !isAdmin || !newRole.name.trim()">Rolle erstellen</button>
                </div>
              </div>

              <div class="grid">
                @for (role of allRoles; track role.id) {
                  <div class="card">
                    <div class="row flex-between">
                      <div>
                        <strong>{{ role.name }}</strong>
                        <div class="muted">{{ role.description }}</div>
                      </div>
                      <button class="danger btn-sm-action" (click)="deleteRole(role.id)" [disabled]="!isAdmin">Loeschen</button>
                    </div>
                  </div>
                }
              </div>
            </div>
          }
        </div>
      }
    </div>
  `,
})
export class TeamsComponent implements OnInit {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(AdminFacade);
  private ns = inject(NotificationService);
  private userAuth = inject(UserAuthService);

  currentTab: 'blueprints' | 'teams' | 'advanced' = 'blueprints';
  advancedTab: 'teams' | 'types' | 'roles' = 'teams';

  isAdmin = false;
  busy = false;
  loading = false;
  blueprints: any[] = [];
  teams: any[] = [];
  templates: any[] = [];
  teamTypesList: any[] = [];
  allRoles: any[] = [];
  selectedBlueprintId = '';

  newType: any = { name: '', description: '' };
  newRole: any = { name: '', description: '', default_template_id: '' };
  newTeam: any = { id: '', name: '', team_type_id: '', description: '', members: [] as any[] };
  blueprintForm: any = this.emptyBlueprintForm();
  teamFromBlueprint: any = this.emptyTeamFromBlueprint();

  hub = this.dir.list().find(a => a.role === 'hub');
  allAgents = this.dir.list();
  private refreshSafetyTimer?: ReturnType<typeof setTimeout>;

  ngOnInit() {
    this.userAuth.user$.subscribe(user => {
      this.isAdmin = user?.role === 'admin';
    });
    this.refresh();
  }

  get selectedInstantiateBlueprint() {
    return this.blueprints.find(blueprint => blueprint.id === this.teamFromBlueprint.blueprint_id) || null;
  }

  refresh() {
    if (!this.hub) return;
    if (this.refreshSafetyTimer) {
      clearTimeout(this.refreshSafetyTimer);
      this.refreshSafetyTimer = undefined;
    }
    this.loading = true;
    this.refreshSafetyTimer = setTimeout(() => {
      this.loading = false;
      this.refreshSafetyTimer = undefined;
      this.ns.info('Teams-Ansicht wurde mit Safe-Timeout entsperrt. Sie koennen weiterarbeiten.');
    }, 20000);
    let pending = 6;
    const done = () => {
      pending -= 1;
      if (pending <= 0) {
        this.loading = false;
        if (this.refreshSafetyTimer) {
          clearTimeout(this.refreshSafetyTimer);
          this.refreshSafetyTimer = undefined;
        }
      }
    };

    this.hubApi.listBlueprints(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => {
        this.blueprints = this.normalizeListResponse(r);
        this.keepSelectionsStable();
      },
      error: () => this.ns.error('Blueprints konnten nicht geladen werden'),
    });

    this.hubApi.listTeams(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => {
        this.teams = this.normalizeListResponse(r);
        this.allAgents = this.dir.list();
      },
      error: () => this.ns.error('Teams konnten nicht geladen werden'),
    });

    this.hubApi.listTemplates(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => (this.templates = this.normalizeListResponse(r)),
      error: () => this.ns.error('Templates konnten nicht geladen werden'),
    });

    this.hubApi.listTeamTypes(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => (this.teamTypesList = this.normalizeListResponse(r)),
      error: () => this.ns.error('Team-Typen konnten nicht geladen werden'),
    });

    this.hubApi.listTeamRoles(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => (this.allRoles = this.normalizeListResponse(r)),
      error: () => this.ns.error('Rollen konnten nicht geladen werden'),
    });

    this.hubApi.listAgents(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => {
        const data = Array.isArray(r) ? r : r?.items || r?.data || [];
        this.allAgents = Array.isArray(data) ? data : this.dir.list();
      },
      error: () => {
        this.allAgents = this.dir.list();
      },
    });
  }

  normalizeListResponse(value: any): any[] {
    return Array.isArray(value) ? value : [];
  }

  emptyBlueprintForm() {
    return {
      id: '',
      name: '',
      description: '',
      base_team_type_name: '',
      roles: [] as BlueprintRoleForm[],
      artifacts: [] as BlueprintArtifactForm[],
    };
  }

  emptyTeamFromBlueprint() {
    return {
      blueprint_id: '',
      name: '',
      description: '',
      activate: true,
      members: [] as any[],
    };
  }

  startNewBlueprint() {
    this.selectedBlueprintId = '';
    this.blueprintForm = this.emptyBlueprintForm();
  }

  selectBlueprint(blueprint: any) {
    this.selectedBlueprintId = blueprint.id;
    this.blueprintForm = {
      id: blueprint.id,
      name: blueprint.name || '',
      description: blueprint.description || '',
      base_team_type_name: blueprint.base_team_type_name || '',
      roles: (blueprint.roles || []).map((role: any) => ({
        id: role.id,
        name: role.name || '',
        description: role.description || '',
        template_id: role.template_id || '',
        sort_order: role.sort_order || 0,
        is_required: role.is_required !== false,
        config: role.config || {},
      })),
      artifacts: (blueprint.artifacts || []).map((artifact: any) => ({
        id: artifact.id,
        kind: artifact.kind || 'task',
        title: artifact.title || '',
        description: artifact.description || '',
        sort_order: artifact.sort_order || 0,
        payload: {
          status: artifact.payload?.status || 'todo',
          priority: artifact.payload?.priority || 'Medium',
        },
      })),
    };
  }

  prepareInstantiateFromEditor() {
    if (!this.blueprintForm.id) return;
    this.currentTab = 'teams';
    this.onInstantiateBlueprintChange(this.blueprintForm.id);
    if (!this.teamFromBlueprint.name) {
      this.teamFromBlueprint.name = `${this.blueprintForm.name} Team`;
    }
  }

  isSeedBlueprint(id: string): boolean {
    return !!this.blueprints.find(blueprint => blueprint.id === id && blueprint.is_seed);
  }

  isSelectedSeedBlueprint(): boolean {
    return !!this.blueprintForm.id && this.isSeedBlueprint(this.blueprintForm.id);
  }

  addBlueprintRole() {
    this.blueprintForm.roles.push({
      name: '',
      description: '',
      template_id: '',
      sort_order: (this.blueprintForm.roles.length + 1) * 10,
      is_required: true,
      config: {},
    });
  }

  removeBlueprintRole(index: number) {
    this.blueprintForm.roles.splice(index, 1);
  }

  addBlueprintArtifact() {
    this.blueprintForm.artifacts.push({
      kind: 'task',
      title: '',
      description: '',
      sort_order: (this.blueprintForm.artifacts.length + 1) * 10,
      payload: { status: 'todo', priority: 'Medium' },
    });
  }

  removeBlueprintArtifact(index: number) {
    this.blueprintForm.artifacts.splice(index, 1);
  }

  saveBlueprint() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;

    const payload = {
      name: this.blueprintForm.name?.trim(),
      description: this.blueprintForm.description || undefined,
      base_team_type_name: this.blueprintForm.base_team_type_name || undefined,
      roles: this.blueprintForm.roles.map((role: BlueprintRoleForm) => ({
        name: role.name?.trim(),
        description: role.description || undefined,
        template_id: role.template_id || undefined,
        sort_order: Number(role.sort_order || 0),
        is_required: role.is_required !== false,
        config: role.config || {},
      })),
      artifacts: this.blueprintForm.artifacts.map((artifact: BlueprintArtifactForm) => ({
        kind: artifact.kind || 'task',
        title: artifact.title?.trim(),
        description: artifact.description || undefined,
        sort_order: Number(artifact.sort_order || 0),
        payload: artifact.payload || {},
      })),
    };

    this.busy = true;
    const request$ = this.blueprintForm.id
      ? this.hubApi.patchBlueprint(this.hub.url, this.blueprintForm.id, payload)
      : this.hubApi.createBlueprint(this.hub.url, payload);

    request$.pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => {
        this.ns.success(this.blueprintForm.id ? 'Blueprint gespeichert' : 'Blueprint erstellt');
        this.refresh();
      },
      error: (err) => this.handleTeamError(err, 'Blueprint konnte nicht gespeichert werden'),
    });
  }

  deleteBlueprint(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Blueprint wirklich loeschen?')) return;
    this.busy = true;
    this.hubApi.deleteBlueprint(this.hub.url, id).pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => {
        this.ns.success('Blueprint geloescht');
        this.startNewBlueprint();
        this.refresh();
      },
      error: (err) => this.handleTeamError(err, 'Blueprint konnte nicht geloescht werden'),
    });
  }

  onInstantiateBlueprintChange(blueprintId: string) {
    this.teamFromBlueprint.blueprint_id = blueprintId;
    this.teamFromBlueprint.members = [];
    const blueprint = this.blueprints.find(item => item.id === blueprintId);
    if (!blueprint) return;
    this.selectedBlueprintId = blueprint.id;
    if (!this.teamFromBlueprint.name) this.teamFromBlueprint.name = `${blueprint.name} Team`;
    if (!this.teamFromBlueprint.description) this.teamFromBlueprint.description = blueprint.description || '';
  }

  addInstantiateMember() {
    this.teamFromBlueprint.members.push({ blueprint_role_id: '', agent_url: '', custom_template_id: '' });
  }

  removeInstantiateMember(index: number) {
    this.teamFromBlueprint.members.splice(index, 1);
  }

  instantiateBlueprint() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !this.teamFromBlueprint.blueprint_id) return;

    const payload = {
      name: this.teamFromBlueprint.name?.trim(),
      description: this.teamFromBlueprint.description || undefined,
      activate: !!this.teamFromBlueprint.activate,
      members: this.teamFromBlueprint.members
        .filter((member: any) => member.agent_url || member.blueprint_role_id || member.custom_template_id)
        .map((member: any) => ({
          agent_url: member.agent_url,
          blueprint_role_id: member.blueprint_role_id || undefined,
          custom_template_id: member.custom_template_id || undefined,
        })),
    };

    this.busy = true;
    this.hubApi.instantiateBlueprint(this.hub.url, this.teamFromBlueprint.blueprint_id, payload)
      .pipe(finalize(() => (this.busy = false)))
      .subscribe({
        next: () => {
          this.ns.success('Team aus Blueprint erstellt');
          this.teamFromBlueprint = this.emptyTeamFromBlueprint();
          this.refresh();
        },
        error: (err) => this.handleTeamError(err, 'Team konnte nicht aus Blueprint erstellt werden'),
      });
  }

  useSeedBlueprint(name: string) {
    const blueprint = this.blueprints.find(item => item.name === name);
    if (!blueprint) {
      this.ns.error(`Seed-Blueprint nicht gefunden: ${name}`);
      return;
    }
    this.currentTab = 'teams';
    this.teamFromBlueprint = this.emptyTeamFromBlueprint();
    this.teamFromBlueprint.name = `${name} Team`;
    this.onInstantiateBlueprintChange(blueprint.id);
  }

  prepareTeamEdit(team: any) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    this.currentTab = 'advanced';
    this.advancedTab = 'teams';
    this.newTeam = {
      id: team.id,
      name: team.name || '',
      description: team.description || '',
      team_type_id: team.team_type_id || '',
      members: (team.members || []).map((member: any) => ({ ...member })),
    };
  }

  resetForm() {
    this.newTeam = { id: '', name: '', team_type_id: '', description: '', members: [] };
  }

  addManualMember() {
    this.newTeam.members.push({ agent_url: '', role_id: '', custom_template_id: '' });
  }

  createTeamType() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.busy = true;
    this.hubApi.createTeamType(this.hub.url, this.newType).pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => {
        this.ns.success('Team-Typ erstellt');
        this.newType = { name: '', description: '' };
        this.refresh();
      },
      error: () => this.ns.error('Fehler beim Erstellen des Team-Typs'),
    });
  }

  deleteTeamType(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Team-Typ wirklich loeschen?')) return;
    this.hubApi.deleteTeamType(this.hub.url, id).subscribe({
      next: () => {
        this.ns.success('Team-Typ geloescht');
        this.refresh();
      },
    });
  }

  createRole() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.busy = true;
    this.hubApi.createRole(this.hub.url, this.newRole).pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => {
        this.ns.success('Rolle erstellt');
        this.newRole = { name: '', description: '', default_template_id: '' };
        this.refresh();
      },
      error: () => this.ns.error('Fehler beim Erstellen der Rolle'),
    });
  }

  deleteRole(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Rolle wirklich loeschen?')) return;
    this.hubApi.deleteRole(this.hub.url, id).subscribe({
      next: () => {
        this.ns.success('Rolle geloescht');
        this.refresh();
      },
    });
  }

  toggleRoleForType(typeId: string, roleId: string, isLinked: boolean) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.busy = true;
    const request$ = isLinked ? this.hubApi.unlinkRoleFromType(this.hub.url, typeId, roleId) : this.hubApi.linkRoleToType(this.hub.url, typeId, roleId);
    request$.pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => this.refresh(),
      error: () => this.ns.error('Aenderung konnte nicht gespeichert werden'),
    });
  }

  isRoleLinked(type: any, roleId: string): boolean {
    return type.role_ids && type.role_ids.includes(roleId);
  }

  getRolesForType(typeId: string): any[] {
    if (!typeId) return this.allRoles;
    const type = this.teamTypesList.find(t => t.id === typeId);
    if (!type || !type.role_ids || !type.role_ids.length) return this.allRoles;
    return this.allRoles.filter(role => type.role_ids.includes(role.id));
  }

  getRoleTemplateMapping(typeId: string, roleId: string): string {
    const type = this.teamTypesList.find(t => t.id === typeId);
    return type?.role_templates?.[roleId] || '';
  }

  setRoleTemplateMapping(typeId: string, roleId: string, templateId: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.hubApi.updateRoleTemplateMapping(this.hub.url, typeId, roleId, templateId || null).subscribe({
      next: () => this.refresh(),
      error: () => this.ns.error('Template-Zuordnung konnte nicht gespeichert werden'),
    });
  }

  createTeam() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    const payload = {
      name: this.newTeam.name,
      description: this.newTeam.description,
      team_type_id: this.newTeam.team_type_id || undefined,
      members: (this.newTeam.members || []).map((member: any) => ({
        agent_url: member.agent_url,
        role_id: member.role_id,
        custom_template_id: member.custom_template_id || undefined,
      })),
    };

    this.busy = true;
    const request$ = this.newTeam.id ? this.hubApi.patchTeam(this.hub.url, this.newTeam.id, payload) : this.hubApi.createTeam(this.hub.url, payload);
    request$.pipe(finalize(() => (this.busy = false))).subscribe({
      next: () => {
        this.ns.success(this.newTeam.id ? 'Team aktualisiert' : 'Team erstellt');
        this.resetForm();
        this.refresh();
      },
      error: (err) => this.handleTeamError(err, 'Fehler beim Speichern'),
    });
  }

  activate(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.hubApi.activateTeam(this.hub.url, id).subscribe({
      next: () => {
        this.ns.success('Team aktiviert');
        this.refresh();
      },
    });
  }

  deleteTeam(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Team wirklich loeschen?')) return;
    this.hubApi.deleteTeam(this.hub.url, id).subscribe({
      next: () => {
        this.ns.success('Team geloescht');
        this.refresh();
      },
    });
  }

  availableAgents(team: any) {
    const memberUrls = (team.members || []).map((member: any) => member.agent_url);
    return this.allAgents.filter(agent => !memberUrls.includes(agent.url) && agent.role !== 'hub');
  }

  availableBlueprintAgents() {
    const selectedUrls = this.teamFromBlueprint.members.map((member: any) => member.agent_url).filter(Boolean);
    return this.allAgents.filter(agent => !selectedUrls.includes(agent.url) && agent.role !== 'hub');
  }

  removeMemberFromForm(index: number) {
    this.newTeam.members.splice(index, 1);
  }

  getAgentNameByUrl(url: string): string {
    return this.allAgents.find(agent => agent.url === url)?.name || url;
  }

  getTeamTypeName(id: string): string {
    return this.teamTypesList.find(type => type.id === id)?.name || 'Kein Typ';
  }

  getRoleName(id: string): string {
    return this.allRoles.find(role => role.id === id)?.name || 'Keine Rolle';
  }

  getBlueprintName(id: string): string {
    return this.blueprints.find(blueprint => blueprint.id === id)?.name || '';
  }

  getBlueprintRoleName(team: any, blueprintRoleId: string): string {
    const role = team?.blueprint_snapshot?.roles?.find((item: any) => item.id === blueprintRoleId);
    return role?.name || 'Blueprint-Rolle';
  }

  keepSelectionsStable() {
    if (this.selectedBlueprintId) {
      const blueprint = this.blueprints.find(item => item.id === this.selectedBlueprintId);
      if (blueprint) this.selectBlueprint(blueprint);
    }
    if (this.teamFromBlueprint.blueprint_id && !this.blueprints.find(item => item.id === this.teamFromBlueprint.blueprint_id)) {
      this.teamFromBlueprint = this.emptyTeamFromBlueprint();
    }
  }

  private handleTeamError(err: any, fallback: string) {
    const message = err?.error?.message;
    const data = err?.error?.data || {};
    const hints: Record<string, string> = {
      team_type_not_found: 'Team-Typ nicht gefunden.',
      role_not_found: data.role_id ? `Rolle nicht gefunden: ${data.role_id}` : 'Rolle nicht gefunden.',
      invalid_role_for_team_type: data.role_id ? `Rolle nicht erlaubt: ${data.role_id}` : 'Rolle nicht fuer Team-Typ erlaubt.',
      template_not_found: data.template_id ? `Template nicht gefunden: ${data.template_id}` : 'Template nicht gefunden.',
      role_id_required: 'Rollen-ID erforderlich.',
      blueprint_in_use: data.team_count ? `Blueprint wird noch von ${data.team_count} Team(s) verwendet.` : 'Blueprint wird noch verwendet.',
      blueprint_name_exists: 'Ein Blueprint mit diesem Namen existiert bereits.',
      blueprint_name_required: 'Blueprint-Name ist erforderlich.',
      blueprint_role_not_found: data.blueprint_role_id ? `Blueprint-Rolle nicht gefunden: ${data.blueprint_role_id}` : 'Blueprint-Rolle nicht gefunden.',
      duplicate_blueprint_role_name: data.role_name ? `Blueprint-Rolle doppelt: ${data.role_name}` : 'Blueprint-Rollen muessen eindeutig sein.',
      duplicate_blueprint_role_sort_order: data.sort_order ? `Blueprint-Rollen-Sortierung doppelt: ${data.sort_order}` : 'Blueprint-Rollen-Sortierungen muessen eindeutig sein.',
      blueprint_role_name_required: 'Jede Blueprint-Rolle benoetigt einen Namen.',
      duplicate_blueprint_artifact_title: data.title ? `Blueprint-Artefakt doppelt: ${data.title}` : 'Blueprint-Artefakt-Titel muessen eindeutig sein.',
      duplicate_blueprint_artifact_sort_order: data.sort_order ? `Blueprint-Artefakt-Sortierung doppelt: ${data.sort_order}` : 'Blueprint-Artefakt-Sortierungen muessen eindeutig sein.',
      blueprint_artifact_title_required: 'Jedes Blueprint-Artefakt benoetigt einen Titel.',
      blueprint_artifact_kind_required: 'Jedes Blueprint-Artefakt benoetigt einen Typ.',
      blueprint_artifact_kind_invalid: data.kind ? `Blueprint-Artefakt-Typ ungueltig: ${data.kind}` : 'Blueprint-Artefakt-Typ ungueltig.',
    };
    if (message && hints[message]) {
      this.ns.error(hints[message]);
      return;
    }
    if (message) {
      this.ns.error(message);
      return;
    }
    this.ns.error(fallback);
  }
}
