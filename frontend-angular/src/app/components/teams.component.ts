import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { UiSkeletonComponent } from './ui-skeleton.component';
import { AdminFacade } from '../features/admin/admin.facade';
import { recommendBlueprint } from '../shared/blueprint-recommendation';

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

type GuidedSetupForm = {
  goal_type: string;
  strictness: string;
  domain: string;
  execution_style: string;
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
          <h2 class="teams-title">Starte mit einem Blueprint und instanziiere daraus ein Team.</h2>
          <p class="teams-copy">
            Der Standardweg bleibt kompakt: Blueprint waehlen, Team starten, dann bei Bedarf im Advanced-Modus vertiefen.
          </p>
        </div>
      <div class="teams-hero-actions">
        <button class="btn-primary" (click)="currentTab = 'blueprints'">Blueprints</button>
        <button class="btn-secondary" (click)="currentTab = 'teams'">Team erstellen</button>
        @if (isAdmin) {
          <button
            class="btn-secondary"
            [class.btn-primary]="viewMode === 'standard'"
            (click)="setViewMode('standard')"
          >
            Standard-Modus
          </button>
          <button
            class="btn-secondary"
            [class.btn-primary]="viewMode === 'admin'"
            (click)="setViewMode('admin')"
          >
            Admin-/Studio-Modus
          </button>
        }
        <button class="btn-secondary" (click)="refresh()">Aktualisieren</button>
      </div>
      </div>

      <div class="tabs teams-tabs">
        <button type="button" class="tab" [class.active]="currentTab === 'blueprints'" (click)="currentTab = 'blueprints'">Blueprints</button>
        <button type="button" class="tab" [class.active]="currentTab === 'teams'" (click)="currentTab = 'teams'">Teams aus Blueprint</button>
        @if (isAdmin && viewMode === 'admin') {
          <button type="button" class="tab" [class.active]="currentTab === 'advanced'" (click)="currentTab = 'advanced'">Advanced</button>
        }
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
              <h3 class="no-margin">Standard-Blueprint-Katalog</h3>
              @if (isAdmin && viewMode === 'admin') {
                <button class="btn-secondary btn-small" (click)="startNewBlueprint()" [disabled]="!isAdmin">Neu</button>
              }
            </div>
            <p class="muted no-margin">
              {{ blueprintCatalogModel?.default_entry_path || 'Standardweg: Blueprint waehlen und Team instanziieren.' }}
            </p>

            <div class="teams-blueprint-list">
              @for (blueprint of catalogBlueprintCards(); track blueprint.id) {
                <button type="button" class="teams-blueprint-card" [class.selected]="selectedBlueprintId === blueprint.id" (click)="selectBlueprintFromCatalog(blueprint)">
                  <div class="row flex-between">
                    <strong>{{ blueprint.name }}</strong>
                    @if (blueprint.is_standard_blueprint || blueprint.is_seed) {
                      <span class="teams-pill teams-pill-seed">Standard</span>
                    }
                  </div>
                  <p class="muted teams-blueprint-desc">{{ blueprint.short_description || blueprint.description || 'Keine Beschreibung' }}</p>
                  <div class="muted teams-blueprint-meta">
                    Einsatzzweck: {{ blueprint.intended_use || 'Wiederverwendbare Team-Initialisierung' }}
                  </div>
                  <div class="muted teams-blueprint-meta">Wann nutzen: {{ blueprint.when_to_use || 'Bei wiederholbaren Team-Starts' }}</div>
                  <div class="muted teams-blueprint-meta">Status: {{ blueprintLifecycleLabel(blueprint) }}</div>
                  <div class="muted teams-blueprint-meta">{{ blueprintLifecycleHint(blueprint) }}</div>
                  <div class="muted teams-blueprint-meta">Erwartete Outputs: {{ formatExpectedOutputs(blueprint.expected_outputs) }}</div>
                  <div class="muted teams-blueprint-meta">Sicherheits-/Review-Stance: {{ blueprint.safety_review_stance || 'balanced security, standard verification' }}</div>
                </button>
              }
            </div>
          </div>

          @if (isAdmin && viewMode === 'admin') {
          <div class="card teams-editor-panel">
            <div class="row flex-between">
              <div>
                <h3 class="no-margin">{{ blueprintForm.id ? 'Blueprint bearbeiten' : 'Neuen Blueprint anlegen' }}</h3>
                <div class="muted">Advanced-Editor fuer Rollen, Rollen-Templates und Starter-Artefakte.</div>
                <div class="muted">Hinweis: Hier wird Team-Struktur bearbeitet. Rollenverhalten bearbeitest du separat unter <code>Templates (Hub)</code>.</div>
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
                    <label>Rollen-Template
                      <select [(ngModel)]="role.template_id" [disabled]="!isAdmin">
                        <option value="">-- Kein Rollen-Template --</option>
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
          } @else {
          <div class="card teams-summary-card">
            <h3 class="no-margin">Standard-Modus: Blueprint verstehen und starten</h3>
            <p class="muted">
              Der Standard-Modus zeigt nur die entscheidenden Produktinfos fuer den Start. Detailpflege bleibt im Admin-/Studio-Modus.
            </p>
            <div class="teams-inline-card">
              <h4 class="no-margin">Gefuehrte Blueprint-Auswahl</h4>
              <div class="muted">
                Fuer den Start reichen vier Angaben: Zieltyp, Striktheit, Domaene und Ausfuehrungsstil.
              </div>
              <div class="grid cols-2 mt-md">
                <label>Zieltyp
                  <select [(ngModel)]="guidedSetup.goal_type">
                    @for (option of guidedGoalTypeOptions; track option.value) {
                      <option [value]="option.value">{{ option.label }}</option>
                    }
                  </select>
                </label>
                <label>Striktheit
                  <select [(ngModel)]="guidedSetup.strictness">
                    @for (option of guidedStrictnessOptions; track option.value) {
                      <option [value]="option.value">{{ option.label }}</option>
                    }
                  </select>
                </label>
                <label>Domaene
                  <select [(ngModel)]="guidedSetup.domain">
                    @for (option of guidedDomainOptions; track option.value) {
                      <option [value]="option.value">{{ option.label }}</option>
                    }
                  </select>
                </label>
                <label>Ausfuehrungsstil
                  <select [(ngModel)]="guidedSetup.execution_style">
                    @for (option of guidedExecutionStyleOptions; track option.value) {
                      <option [value]="option.value">{{ option.label }}</option>
                    }
                  </select>
                </label>
              </div>
              @if (guidedSetupRecommendation() as recommendation) {
                <div class="teams-inline-card mt-md">
                  <div class="row flex-between">
                    <strong>Empfehlter Blueprint: {{ recommendation.card.name }}</strong>
                    <span class="teams-pill teams-pill-seed">Empfehlung</span>
                  </div>
                  <div class="muted teams-blueprint-desc">
                    {{ recommendation.card.short_description || recommendation.card.intended_use || 'Produktnaher Standard-Blueprint.' }}
                  </div>
                  <div class="teams-summary-meta">Warum: {{ recommendation.reasons.join(' ') }}</div>
                  <div class="teams-summary-meta">
                    Work-Profile: {{ formatPreviewList(recommendation.card.work_profile_summary?.recommended_goal_modes, 'Standard-Modi') }}
                  </div>
                  <div class="teams-summary-meta">{{ recommendation.reviewNote }}</div>
                  <div class="row mt-sm">
                    <button class="btn-primary" (click)="applyGuidedSetupRecommendation()">Empfehlung fuer Team-Start uebernehmen</button>
                  </div>
                </div>
              }
            </div>
            @if (selectedCatalogBlueprintCard()) {
              <div class="teams-inline-card">
                <div class="row flex-between">
                  <strong>{{ selectedCatalogBlueprintCard()?.name }}</strong>
                  @if (selectedCatalogBlueprintCard()?.is_standard_blueprint) {
                    <span class="teams-pill teams-pill-seed">Standard</span>
                  }
                </div>
                <p class="muted teams-blueprint-desc">
                  {{ selectedCatalogBlueprintCard()?.short_description || selectedCatalogBlueprintCard()?.intended_use || 'Keine Beschreibung' }}
                </p>
                <div class="teams-summary-meta">
                  Goal-Modi: {{ formatPreviewList(selectedCatalogBlueprintCard()?.work_profile_summary?.recommended_goal_modes, 'Standard-Modi') }}
                </div>
                <div class="teams-summary-meta">
                  Playbooks: {{ formatPreviewList(selectedCatalogBlueprintCard()?.work_profile_summary?.playbook_hints, 'Keine speziellen Playbooks') }}
                </div>
                <div class="teams-summary-meta">
                  Capabilities: {{ formatPreviewList(selectedCatalogBlueprintCard()?.work_profile_summary?.capability_hints, 'Standard-Capabilities') }}
                </div>
                <div class="teams-summary-meta">
                  Governance: {{ selectedCatalogBlueprintCard()?.work_profile_summary?.governance_profile?.label || 'Balanced default profile' }}
                </div>
                <div class="teams-summary-meta">
                  {{ selectedCatalogBlueprintCard()?.work_profile_summary?.governance_profile?.hint || 'Standardprofil fuer kontrollierte Ausfuehrung.' }}
                </div>
              </div>
              <div class="row">
                <button class="btn-primary" (click)="prepareInstantiateFromEditor()">Als Team starten</button>
              </div>
            } @else {
              <div class="state-banner info">Waehle links einen Blueprint, um den Team-Start vorzubereiten.</div>
            }
          </div>
          }
        </div>
      }

      @if (currentTab === 'teams') {
        <div class="grid cols-2 teams-blueprint-grid">
          <div class="card card-success">
            <h3 class="no-margin">Team aus Blueprint erstellen</h3>
            <p class="muted">
              Blueprint = wiederverwendbare Basis. Team = laufende Instanz fuer die konkrete Ausfuehrung.
            </p>

            <div class="grid cols-2">
              <label>Blueprint
                <select [(ngModel)]="teamFromBlueprint.blueprint_id" (ngModelChange)="onInstantiateBlueprintChange($event)" [disabled]="!isAdmin">
                  <option value="">-- Blueprint waehlen --</option>
                  @for (blueprint of catalogBlueprintCards(); track blueprint.id) {
                    <option [value]="blueprint.id">{{ blueprint.name }}</option>
                  }
                </select>
              </label>
              <label>Teamname <input [(ngModel)]="teamFromBlueprint.name" [disabled]="!isAdmin"></label>
              <label class="col-span-full">Beschreibung (optional) <textarea [(ngModel)]="teamFromBlueprint.description" rows="2" [disabled]="!isAdmin"></textarea></label>
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
                <div class="teams-summary-meta">{{ selectedInstantiateBlueprint.base_team_type_name || 'Kein Basis-Typ' }} · {{ selectedInstantiateBlueprint.roles?.length || 0 }} Rollen · {{ selectedInstantiateBlueprint.artifacts?.length || 0 }} Starter-Elemente</div>
                <div class="teams-summary-meta">
                  Start-Rollen: {{ previewBlueprintRoles(selectedInstantiateBlueprint) }}
                </div>
                <div class="teams-summary-meta">
                  Start-Aufgaben: {{ previewBlueprintStartTasks(selectedInstantiateBlueprint) }}
                </div>
                @if (selectedCatalogBlueprintCard()) {
                  <div class="teams-summary-meta">
                    Empfohlene Goal-Modi: {{ formatPreviewList(selectedCatalogBlueprintCard()?.work_profile_summary?.recommended_goal_modes, 'Standard-Modi') }}
                  </div>
                  <div class="teams-summary-meta">
                    Playbook-Hinweise: {{ formatPreviewList(selectedCatalogBlueprintCard()?.work_profile_summary?.playbook_hints, 'Keine speziellen Playbooks') }}
                  </div>
                  <div class="teams-summary-meta">
                    Capability-Hinweise: {{ formatPreviewList(selectedCatalogBlueprintCard()?.work_profile_summary?.capability_hints, 'Standard-Capabilities') }}
                  </div>
                  <div class="teams-summary-meta">
                    Governance-Profil: {{ selectedCatalogBlueprintCard()?.work_profile_summary?.governance_profile?.label || 'Balanced default profile' }}
                  </div>
                }
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
                          <option value="">-- Work Role waehlen --</option>
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
                      <label>Rollen-Template (optional)
                        <select [(ngModel)]="member.custom_template_id" [disabled]="!isAdmin">
                          <option value="">-- Standard Rollen-Template --</option>
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
              <h3 class="no-margin">Laufende Teams</h3>
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
                      <span class="badge badge-gray">{{ teamLifecycleLabel(team) }}</span>
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
                  <div class="muted teams-team-meta">{{ teamLifecycleHint(team) }}</div>
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

      @if (currentTab === 'advanced' && isAdmin && viewMode === 'admin') {
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
                        <label>Rollen-Template Override
                          <select [(ngModel)]="member.custom_template_id" [disabled]="!isAdmin">
                            <option value="">-- Standard Rollen-Template --</option>
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
                            <option value="">-- Rollen-Template --</option>
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
                  <label>Standard Rollen-Template
                    <select [(ngModel)]="newRole.default_template_id" [disabled]="!isAdmin">
                      <option value="">-- Kein Rollen-Template --</option>
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
  viewMode: 'standard' | 'admin' = 'standard';
  advancedTab: 'teams' | 'types' | 'roles' = 'teams';

  isAdmin = false;
  busy = false;
  loading = false;
  blueprints: any[] = [];
  blueprintCatalog: any[] = [];
  blueprintCatalogModel: any = null;
  teams: any[] = [];
  templates: any[] = [];
  teamTypesList: any[] = [];
  allRoles: any[] = [];
  selectedBlueprintId = '';
  guidedSetup: GuidedSetupForm = this.emptyGuidedSetup();

  readonly guidedGoalTypeOptions = [
    { value: 'new_feature', label: 'Neues Feature / Weiterentwicklung' },
    { value: 'bugfix', label: 'Bugfix / Incident' },
    { value: 'research', label: 'Research / Analyse' },
    { value: 'security_review', label: 'Security / Compliance Review' },
    { value: 'release_prep', label: 'Release-Vorbereitung' },
  ];
  readonly guidedStrictnessOptions = [
    { value: 'safe', label: 'Vorsichtig' },
    { value: 'balanced', label: 'Ausgewogen' },
    { value: 'strict', label: 'Strikt' },
  ];
  readonly guidedDomainOptions = [
    { value: 'software', label: 'Software' },
    { value: 'security', label: 'Security' },
    { value: 'release', label: 'Release' },
    { value: 'general', label: 'Allgemein' },
  ];
  readonly guidedExecutionStyleOptions = [
    { value: 'iterative', label: 'Iterativ (Sprint/Loop)' },
    { value: 'flow', label: 'Flow/Kanban' },
    { value: 'opencode', label: 'OpenCode/Execution-Kaskade' },
    { value: 'evolution', label: 'Research -> Evolution' },
  ];

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
      if (!this.isAdmin) this.viewMode = 'standard';
    });
    this.refresh();
  }

  setViewMode(mode: 'standard' | 'admin') {
    this.viewMode = mode;
    if (mode === 'standard' && this.currentTab === 'advanced') this.currentTab = 'blueprints';
  }

  get selectedInstantiateBlueprint() {
    return this.blueprints.find(blueprint => blueprint.id === this.teamFromBlueprint.blueprint_id) || null;
  }

  selectedCatalogBlueprintCard() {
    if (!this.selectedBlueprintId) return null;
    return this.catalogBlueprintCards().find(blueprint => blueprint.id === this.selectedBlueprintId) || null;
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
    let pending = 7;
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

    this.hubApi.listBlueprintCatalog(this.hub.url).pipe(finalize(done)).subscribe({
      next: r => {
        const payload = r && typeof r === 'object' ? r : {};
        this.blueprintCatalog = Array.isArray((payload as any).items)
          ? (payload as any).items
          : [];
        this.blueprintCatalogModel = (payload as any).public_model || null;
      },
      error: () => {
        this.blueprintCatalog = [];
        this.blueprintCatalogModel = null;
      },
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
      error: () => this.ns.error('Rollen-Templates konnten nicht geladen werden'),
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

  emptyGuidedSetup(): GuidedSetupForm {
    return {
      goal_type: 'new_feature',
      strictness: 'balanced',
      domain: 'software',
      execution_style: 'iterative',
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

  selectBlueprintFromCatalog(blueprint: any) {
    const fullBlueprint = this.blueprints.find(item => item.id === blueprint.id);
    if (!fullBlueprint) {
      this.selectedBlueprintId = blueprint.id || '';
      return;
    }
    this.selectBlueprint(fullBlueprint);
  }

  catalogBlueprintCards(): any[] {
    if (this.blueprintCatalog.length > 0) return this.blueprintCatalog;
    return this.blueprints.map(blueprint => ({
      id: blueprint.id,
      name: blueprint.name,
      short_description: blueprint.description || '',
      intended_use: 'Reusable team definition with role setup and starter artifacts.',
      when_to_use: 'Use for repeatable team startup instead of manual assembly.',
      expected_outputs: (blueprint.artifacts || [])
        .filter((artifact: any) => String(artifact?.kind || '').toLowerCase() === 'task')
        .slice(0, 3)
        .map((artifact: any) => artifact.title)
        .filter(Boolean),
      safety_review_stance: 'balanced security, standard verification',
      is_standard_blueprint: !!blueprint.is_seed,
      entry_recommended: !!blueprint.is_seed,
    }));
  }

  formatExpectedOutputs(outputs: any): string {
    const values = Array.isArray(outputs)
      ? outputs.map(item => String(item || '').trim()).filter(Boolean)
      : [];
    return values.length ? values.join(', ') : 'Starter tasks and role-ready execution context';
  }

  formatPreviewList(values: any, fallback: string, limit = 4): string {
    const normalized = Array.isArray(values)
      ? values.map(item => String(item || '').trim()).filter(Boolean).slice(0, limit)
      : [];
    return normalized.length ? normalized.join(', ') : fallback;
  }

  previewBlueprintRoles(blueprint: any, limit = 5): string {
    const roles = Array.isArray(blueprint?.roles)
      ? blueprint.roles.map((role: any) => String(role?.name || '').trim()).filter(Boolean).slice(0, limit)
      : [];
    return roles.length ? roles.join(', ') : 'Keine Start-Rollen';
  }

  previewBlueprintStartTasks(blueprint: any, limit = 5): string {
    const tasks = Array.isArray(blueprint?.artifacts)
      ? blueprint.artifacts
        .filter((artifact: any) => String(artifact?.kind || '').toLowerCase() === 'task')
        .map((artifact: any) => String(artifact?.title || '').trim())
        .filter(Boolean)
        .slice(0, limit)
      : [];
    return tasks.length ? tasks.join(', ') : 'Keine expliziten Start-Aufgaben';
  }

  guidedSetupRecommendation() {
    const recommendation = recommendBlueprint({
      goalType: this.guidedSetup.goal_type,
      strictness: this.guidedSetup.strictness,
      domain: this.guidedSetup.domain,
      executionStyle: this.guidedSetup.execution_style,
    });
    const card = this.blueprintCardByName(recommendation.blueprintName);
    if (!card) return null;
    return {
      card,
      reasons: recommendation.reasons,
      reviewNote: recommendation.reviewNote,
      suggestedTeamName: `${card.name} Team`,
    };
  }

  applyGuidedSetupRecommendation() {
    const recommendation = this.guidedSetupRecommendation();
    if (!recommendation) {
      this.ns.error('Keine passende Blueprint-Empfehlung gefunden');
      return;
    }
    this.selectBlueprintFromCatalog(recommendation.card);
    this.currentTab = 'teams';
    this.onInstantiateBlueprintChange(recommendation.card.id);
    if (!this.teamFromBlueprint.name) this.teamFromBlueprint.name = recommendation.suggestedTeamName;
    this.ns.success(`Empfehlung gesetzt: ${recommendation.card.name}`);
  }

  blueprintLifecycleLabel(blueprint: any): string {
    if (blueprint?.is_standard_blueprint || blueprint?.is_seed) return 'Standard';
    const driftStatus = String(blueprint?.definition_metadata?.drift_status || '').toLowerCase();
    if (driftStatus === 'drifted') return 'Aktualisierbar';
    return 'Angepasst';
  }

  blueprintLifecycleHint(blueprint: any): string {
    if (blueprint?.is_standard_blueprint || blueprint?.is_seed) {
      return 'Standard-Blueprint fuer den produktnahen Einstieg.';
    }
    const driftStatus = String(blueprint?.definition_metadata?.drift_status || '').toLowerCase();
    if (driftStatus === 'drifted') {
      return 'Basisdefinition wurde aktualisiert; diese Variante kann abgeglichen werden.';
    }
    return 'Angepasste Blueprint-Variante fuer spezifische Teambedarfe.';
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
      error: () => this.ns.error('Rollen-Template-Zuordnung konnte nicht gespeichert werden'),
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

  teamLifecycleLabel(team: any): string {
    const state = team?.user_lifecycle_state;
    if (state?.label) return String(state.label);
    const driftStatus = String(team?.definition_metadata?.drift_status || '').toLowerCase();
    if (driftStatus === 'in_sync') return 'Standard';
    if (driftStatus === 'drifted') return 'Aktualisierbar';
    return 'Angepasst';
  }

  teamLifecycleHint(team: any): string {
    const state = team?.user_lifecycle_state;
    if (state?.hint) return String(state.hint);
    const driftStatus = String(team?.definition_metadata?.drift_status || '').toLowerCase();
    if (driftStatus === 'in_sync') return 'Dieses Team folgt dem aktuellen Blueprint-Stand.';
    if (driftStatus === 'drifted') return 'Blueprint wurde aktualisiert; diese Instanz nutzt noch den vorherigen Stand.';
    return 'Diese laufende Team-Instanz wurde individuell angepasst.';
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
      template_not_found: data.template_id ? `Rollen-Template nicht gefunden: ${data.template_id}` : 'Rollen-Template nicht gefunden.',
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

  private blueprintCardByName(name: string) {
    const normalizedTarget = String(name || '').trim().toLowerCase();
    return this.catalogBlueprintCards().find(card => String(card?.name || '').trim().toLowerCase() === normalizedTarget) || null;
  }
}
