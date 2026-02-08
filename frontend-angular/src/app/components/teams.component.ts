import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AgentDirectoryService, AgentEntry } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';

@Component({
  standalone: true,
  selector: 'app-teams',
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Management</h2>
      <button (click)="refresh()" [disabled]="busy">🔄 Aktualisieren</button>
    </div>

    <div class="tabs">
      <div class="tab" [class.active]="currentTab === 'teams'" (click)="currentTab = 'teams'">Teams</div>
      <div class="tab" [class.active]="currentTab === 'types'" (click)="currentTab = 'types'">Team-Typen</div>
      <div class="tab" [class.active]="currentTab === 'roles'" (click)="currentTab = 'roles'">Rollen</div>
    </div>
    
    <!-- TEAMS TAB -->
    <div *ngIf="currentTab === 'teams'">
      <div class="card grid" style="margin-bottom: 20px; background: #f0f7ff; padding: 15px;">
        <h3>KI Team-Beratung ({{teamAgent?.name || 'Hub'}})</h3>
        <p class="muted">Beschreiben Sie Ihr gewünschtes Team und die KI hilft bei der Konfiguration.</p>
        <div class="row" style="gap: 10px; align-items: flex-end;">
          <label style="flex: 1; margin-bottom: 0;">Ihr Anliegen
            <input [(ngModel)]="aiPrompt" placeholder="z.B. 'Ein 3-köpfiges Team für ein Backend-Projekt'" [disabled]="busy || !isAdmin">
          </label>
          <button (click)="generateAI()" [disabled]="busy || !aiPrompt || !isAdmin" class="button-outline" style="margin-bottom: 0;">🪄 Beraten</button>
        </div>
      </div>

      <div class="card grid" style="margin-bottom: 20px;">
        <h3>Team konfigurieren</h3>
        <div *ngIf="!isAdmin" class="muted" style="margin-bottom: 10px;">
          Team-Verwaltung ist nur für Admins verfügbar.
        </div>
        <div class="grid cols-3">
          <label>Name <input [(ngModel)]="newTeam.name" placeholder="z.B. Scrum Team Alpha" [disabled]="!isAdmin"></label>
          <label>Typ <select [(ngModel)]="newTeam.team_type_id" [disabled]="!isAdmin">
            <option value="">-- Typ wählen --</option>
            <option *ngFor="let type of teamTypesList" [value]="type.id">{{type.name}}</option>
          </select></label>
          <label>Beschreibung <input [(ngModel)]="newTeam.description" placeholder="Ziele des Teams..." [disabled]="!isAdmin"></label>
        </div>

        <div *ngIf="newTeam.name" style="margin-top: 20px; border-top: 1px solid #eee; padding-top: 15px;">
          <h4>Mitglieder & Rollen</h4>
          <div *ngFor="let m of newTeam.members; let i = index" class="row" style="margin-bottom: 8px; align-items: center; gap: 10px;">
            <div style="min-width: 150px;"><strong>{{ getAgentNameByUrl(m.agent_url) }}</strong></div>
            <select [(ngModel)]="m.role_id" style="margin-bottom: 0; flex: 1;" [disabled]="!isAdmin">
              <option value="">-- Rolle wählen --</option>
              <option *ngFor="let role of getRolesForType(newTeam.team_type_id)" [value]="role.id">{{role.name}}</option>
            </select>
            <select [(ngModel)]="m.custom_template_id" style="margin-bottom: 0; flex: 1;" [disabled]="!isAdmin">
              <option value="">-- Standard Template --</option>
              <option *ngFor="let t of templates" [value]="t.id">{{t.name}}</option>
            </select>
            <button (click)="removeMemberFromForm(i)" class="danger" style="padding: 4px 8px;" [disabled]="!isAdmin">×</button>
          </div>
          <div *ngIf="!newTeam.members?.length" class="muted">Fügen Sie unten in der Liste Agenten hinzu.</div>
        </div>

        <div class="row" style="margin-top:10px">
          <button (click)="createTeam()" [disabled]="busy || !newTeam.name || !isAdmin">Speichern</button>
          <button (click)="setupScrum()" [disabled]="busy || !isAdmin" class="button-outline">Scrum Quick-Setup</button>
          <button (click)="resetForm()" class="button-outline" [disabled]="!isAdmin">Neu</button>
        </div>
      </div>

      <div class="grid" *ngIf="teams.length">
        <div class="card" *ngFor="let team of teams" [class.active-team]="team.is_active">
          <div class="row" style="justify-content: space-between; align-items: flex-start;">
            <div>
               <span *ngIf="team.is_active" class="badge">AKTIV</span>
               <strong style="font-size: 1.2em;">{{team.name}}</strong> 
               <span class="muted" style="margin-left: 10px;">({{ getTeamTypeName(team.team_type_id) }})</span>
            </div>
            <div class="row">
              <button (click)="edit(team)" class="button-outline" style="padding: 4px 8px; font-size: 12px;" [disabled]="!isAdmin">Edit</button>
              <button *ngIf="!team.is_active" (click)="activate(team.id)" class="button-outline" style="padding: 4px 8px; font-size: 12px;" [disabled]="!isAdmin">Aktivieren</button>
              <button (click)="deleteTeam(team.id)" class="danger" style="padding: 4px 8px; font-size: 12px;" [disabled]="!isAdmin">Löschen</button>
            </div>
          </div>
          <p class="muted" style="margin: 8px 0;">{{team.description}}</p>
          
          <div style="margin-top: 15px; border-top: 1px solid #eee; padding-top: 10px;">
            <h4 style="margin-bottom: 8px;">Agenten im Team:</h4>
            <div class="row wrap">
               <div *ngFor="let m of team.members" class="agent-chip" style="flex-direction: column; align-items: flex-start;">
                  <div class="row" style="width:100%; justify-content: space-between;">
                    <strong>{{ getAgentNameByUrl(m.agent_url) }}</strong>
                  </div>
                  <div style="font-size: 11px; margin-top: 4px;">
                    <span class="badge" style="background: #007bff; margin-right: 4px;">{{ getRoleName(m.role_id) }}</span>
                    <span *ngIf="m.custom_template_id" class="badge" style="background: #6c757d;">{{ getTemplateName(m.custom_template_id) }}</span>
                  </div>
               </div>
               <div *ngIf="!team.members?.length" class="muted" style="font-style: italic; font-size: 0.9em;">Keine Agenten zugeordnet.</div>
            </div>
          </div>

          <div style="margin-top: 15px;">
            <label style="font-size: 12px; display: block; margin-bottom: 4px;">Agent hinzufügen:</label>
            <div class="row" style="gap: 10px;">
              <select #agentSelect style="flex: 1; margin-bottom: 0;" [disabled]="!isAdmin">
                <option value="">-- Agent wählen --</option>
                <option *ngFor="let a of availableAgents(team)" [value]="a.url">
                    {{a.name}} ({{a.url}})
                </option>
              </select>
              <select #roleSelect style="flex: 1; margin-bottom: 0;" [disabled]="!isAdmin">
                <option value="">-- Rolle --</option>
                <option *ngFor="let role of getRolesForType(team.team_type_id)" [value]="role.id">{{role.name}}</option>
              </select>
              <select #templateSelect style="flex: 1; margin-bottom: 0;" [disabled]="!isAdmin">
                <option value="">-- Template --</option>
                <option *ngFor="let t of templates" [value]="t.id">{{t.name}}</option>
              </select>
              <button (click)="addAgentToTeam(team, agentSelect.value, roleSelect.value, templateSelect.value); agentSelect.value=''; roleSelect.value=''; templateSelect.value=''" 
                      [disabled]="!agentSelect.value || !isAdmin"
                      style="padding: 4px 12px; margin-bottom: 0;">+</button>
            </div>
          </div>
        </div>
      </div>

      <div *ngIf="!teams.length && !busy" class="card muted" style="text-align: center; padding: 40px;">
          Keine Teams vorhanden. Legen Sie oben ein neues Team an.
      </div>
    </div>

    <!-- TEAM TYPES TAB -->
    <div *ngIf="currentTab === 'types'">
      <div class="card grid" style="margin-bottom: 20px;">
        <h3>Team-Typ erstellen</h3>
        <div *ngIf="!isAdmin" class="muted" style="margin-bottom: 10px;">
          Team-Typen können nur von Admins verwaltet werden.
        </div>
        <div class="grid cols-2">
          <label>Name <input [(ngModel)]="newType.name" placeholder="z.B. Scrum Team" [disabled]="!isAdmin"></label>
          <label>Beschreibung <input [(ngModel)]="newType.description" placeholder="Besonderheiten des Typs..." [disabled]="!isAdmin"></label>
        </div>
        <button (click)="createTeamType()" [disabled]="busy || !newType.name || !isAdmin" style="margin-top: 10px;">Typ Erstellen</button>
      </div>

      <div class="grid">
        <div class="card" *ngFor="let type of teamTypesList">
          <div class="row" style="justify-content: space-between;">
            <strong>{{type.name}}</strong>
            <button (click)="deleteTeamType(type.id)" class="danger" style="padding: 4px 8px; font-size: 12px;" [disabled]="!isAdmin">Löschen</button>
          </div>
          <p class="muted">{{type.description}}</p>
          
          <div style="margin-top: 15px; border-top: 1px solid #eee; padding-top: 10px;">
            <h4 style="margin-bottom: 8px;">Zugeordnete Rollen:</h4>
            <div class="row wrap">
              <div *ngFor="let role of allRoles" class="row" style="margin-right: 15px; margin-bottom: 5px; align-items: center; gap: 6px;">
                <input type="checkbox" [checked]="isRoleLinked(type, role.id)" (change)="toggleRoleForType(type.id, role.id, isRoleLinked(type, role.id))" [id]="'link-'+type.id+'-'+role.id" [disabled]="!isAdmin">
                <label [for]="'link-'+type.id+'-'+role.id" style="margin-left: 5px; font-size: 13px;">{{role.name}}</label>
                <select [disabled]="!isAdmin || !isRoleLinked(type, role.id)" [ngModel]="getRoleTemplateMapping(type.id, role.id)"
                        (ngModelChange)="setRoleTemplateMapping(type.id, role.id, $event)" style="font-size: 12px; margin-bottom: 0;">
                  <option value="">-- Template --</option>
                  <option *ngFor="let t of templates" [value]="t.id">{{t.name}}</option>
                </select>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ROLES TAB -->
    <div *ngIf="currentTab === 'roles'">
      <div class="card grid" style="margin-bottom: 20px;">
        <h3>Rolle erstellen</h3>
        <div *ngIf="!isAdmin" class="muted" style="margin-bottom: 10px;">
          Rollen können nur von Admins erstellt oder gelöscht werden.
        </div>
        <div class="grid cols-3">
          <label>Name <input [(ngModel)]="newRole.name" placeholder="z.B. Product Owner" [disabled]="!isAdmin"></label>
          <label>Beschreibung <input [(ngModel)]="newRole.description" placeholder="Aufgaben der Rolle..." [disabled]="!isAdmin"></label>
          <label>Standard Template <select [(ngModel)]="newRole.default_template_id" [disabled]="!isAdmin">
            <option value="">-- Kein Template --</option>
            <option *ngFor="let t of templates" [value]="t.id">{{t.name}}</option>
          </select></label>
        </div>
        <button (click)="createRole()" [disabled]="busy || !newRole.name || !isAdmin" style="margin-top: 10px;">Rolle Erstellen</button>
      </div>

      <div class="grid">
        <div class="card" *ngFor="let role of allRoles">
          <div class="row" style="justify-content: space-between;">
            <strong>{{role.name}}</strong>
            <button (click)="deleteRole(role.id)" class="danger" style="padding: 4px 8px; font-size: 12px;" [disabled]="!isAdmin">Löschen</button>
          </div>
          <p class="muted">{{role.description}}</p>
          <div *ngIf="role.default_template_id" class="muted" style="font-size: 0.8em;">
            Template: {{ getTemplateName(role.default_template_id) }}
          </div>
        </div>
      </div>
    </div>

    <style>
      .active-team { border: 2px solid #28a745 !important; background: #f8fff9; }
      .badge { background: #28a745; color: white; padding: 2px 6px; border-radius: 4px; font-size: 10px; margin-right: 8px; vertical-align: middle; font-weight: bold; }
      .agent-chip { background: #fff; border: 1px solid #ccc; padding: 4px 12px; border-radius: 16px; font-size: 13px; margin-right: 8px; margin-bottom: 8px; display: flex; align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
      .wrap { flex-wrap: wrap; }
      .muted { color: #666; font-size: 0.9em; }
      .tabs { display: flex; gap: 10px; margin-bottom: 20px; border-bottom: 1px solid #ccc; padding-bottom: 10px; margin-top: 20px; }
      .tab { padding: 8px 16px; cursor: pointer; border-radius: 4px; border: 1px solid transparent; font-weight: bold; color: #555; }
      .tab.active { background: #007bff; color: white; border-color: #0056b3; }
      .tab:hover:not(.active) { background: #eee; }
    </style>
  `
})
export class TeamsComponent implements OnInit {
  currentTab: 'teams' | 'types' | 'roles' = 'teams';
  newType: any = { name: '', description: '' };
  newRole: any = { name: '', description: '', default_template_id: '' };
  isAdmin = false;

  teams: any[] = [];
  templates: any[] = [];
  teamTypesList: any[] = [];
  allRoles: any[] = [];
  busy = false;
  aiPrompt = '';
  newTeam: any = { name: '', team_type_id: '', description: '', members: [] };
  hub = this.dir.list().find(a => a.role === 'hub');
  teamAgent: any;
  allAgents = this.dir.list();

  constructor(
    private dir: AgentDirectoryService, 
    private hubApi: HubApiService, 
    private agentApi: AgentApiService,
    private ns: NotificationService,
    private userAuth: UserAuthService
  ) {}

  ngOnInit() {
    this.userAuth.user$.subscribe(user => {
      this.isAdmin = user?.role === 'admin';
    });
    this.refresh();
  }

  generateAI() {
    if (!this.isAdmin || !this.aiPrompt.trim()) return;
    const target = this.teamAgent || this.hub;
    if (!target) return;

    this.busy = true;
    const p = `Berate mich bei der Konfiguration eines Teams für: ${this.aiPrompt}. 
    Antworte im JSON Format mit den Feldern 'name', 'description' und 'team_type_id' (Wähle eine passende aus: ${this.teamTypesList.map(t => t.name + ' [' + t.id + ']').join(', ')}).`;

    this.agentApi.llmGenerate(target.url, p, null, undefined, { context: { team_types: this.teamTypesList, roles: this.allRoles, templates: this.templates } }).subscribe({
      next: r => {
        try {
          let data = r.response;
          if (typeof data === 'string') {
            const start = data.indexOf('{');
            const end = data.lastIndexOf('}');
            if (start !== -1 && end !== -1) {
              data = JSON.parse(data.substring(start, end + 1));
            }
          }
          
          this.newTeam.name = data.name || this.newTeam.name;
          this.newTeam.description = data.description || this.newTeam.description;
          this.newTeam.team_type_id = data.team_type_id || this.newTeam.team_type_id;
          this.ns.success('KI-Vorschlag geladen');
        } catch(e) {
          this.ns.info('KI-Antwort konnte nicht strukturiert geladen werden');
        }
        this.busy = false;
        this.aiPrompt = '';
      },
      error: (e) => {
        const code = e?.error?.error;
        const message = e?.error?.message || e?.message;
        if (code === 'llm_not_configured') {
          this.ns.error('LLM ist nicht konfiguriert (Provider fehlt). Bitte in den Einstellungen nachholen.');
          this.ns.info('Navigieren Sie zu den Einstellungen, um einen LLM-Provider zu wählen.');
        } else if (code === 'llm_api_key_missing') {
          this.ns.error('API-Key für den LLM-Provider fehlt.');
        } else if (code === 'llm_base_url_missing') {
          this.ns.error('LLM Base URL fehlt oder ist leer.');
        } else {
          this.ns.error(message || code || 'KI-Beratung fehlgeschlagen');
        }
        this.busy = false;
      }
    });
  }

  refresh() {
    if (!this.hub) return;
    this.busy = true;

    // Konfiguration laden um Team-Agent zu finden
    this.agentApi.getConfig(this.hub.url).subscribe({
      next: cfg => {
        if (cfg.team_agent_name) {
          this.teamAgent = this.dir.list().find(a => a.name === cfg.team_agent_name);
        } else {
          this.teamAgent = this.hub;
        }
      }
    });

    this.hubApi.listTeams(this.hub.url).subscribe({
      next: r => { this.teams = Array.isArray(r) ? r : []; this.allAgents = this.dir.list(); },
      error: () => this.ns.error('Teams konnten nicht geladen werden')
    });

    this.hubApi.listTemplates(this.hub.url).subscribe({
      next: r => this.templates = Array.isArray(r) ? r : [],
      error: () => this.ns.error('Templates konnten nicht geladen werden')
    });

    this.hubApi.listTeamTypes(this.hub.url).subscribe({
      next: r => this.teamTypesList = Array.isArray(r) ? r : [],
      error: () => this.ns.error('Team-Typen konnten nicht geladen werden')
    });

    this.hubApi.listTeamRoles(this.hub.url).subscribe({
      next: r => this.allRoles = Array.isArray(r) ? r : [],
      error: () => this.ns.error('Rollen konnten nicht geladen werden'),
      complete: () => this.busy = false
    });
  }

  resetForm() {
    this.newTeam = { name: '', team_type_id: '', description: '', members: [] };
  }

  createTeamType() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.busy = true;
    this.hubApi.createTeamType(this.hub.url, this.newType).subscribe({
      next: () => {
        this.ns.success('Team-Typ erstellt');
        this.newType = { name: '', description: '' };
        this.refresh();
      },
      error: () => this.ns.error('Fehler beim Erstellen des Team-Typs'),
      complete: () => this.busy = false
    });
  }

  deleteTeamType(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Team-Typ wirklich löschen?')) return;
    this.hubApi.deleteTeamType(this.hub.url, id).subscribe({
      next: () => { this.ns.success('Team-Typ gelöscht'); this.refresh(); }
    });
  }

  createRole() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.busy = true;
    this.hubApi.createRole(this.hub.url, this.newRole).subscribe({
      next: () => {
        this.ns.success('Rolle erstellt');
        this.newRole = { name: '', description: '', default_template_id: '' };
        this.refresh();
      },
      error: () => this.ns.error('Fehler beim Erstellen der Rolle'),
      complete: () => this.busy = false
    });
  }

  deleteRole(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Rolle wirklich löschen?')) return;
    this.hubApi.deleteRole(this.hub.url, id).subscribe({
      next: () => { this.ns.success('Rolle gelöscht'); this.refresh(); }
    });
  }

  toggleRoleForType(typeId: string, roleId: string, isLinked: boolean) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.busy = true;
    const obs = isLinked 
      ? this.hubApi.unlinkRoleFromType(this.hub.url, typeId, roleId)
      : this.hubApi.linkRoleToType(this.hub.url, typeId, roleId);
    
    obs.subscribe({
      next: () => this.refresh(),
      error: () => this.ns.error('Änderung konnte nicht gespeichert werden'),
      complete: () => this.busy = false
    });
  }

  isRoleLinked(type: any, roleId: string): boolean {
    return type.role_ids && type.role_ids.includes(roleId);
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
    const type = this.teamTypesList.find(t => t.id === typeId);
    if (!type || !this.isRoleLinked(type, roleId)) {
      this.ns.error('Rolle ist nicht mit dem Team-Typ verknüpft');
      return;
    }
    this.hubApi.updateRoleTemplateMapping(this.hub.url, typeId, roleId, templateId || null).subscribe({
      next: () => this.refresh(),
      error: () => this.ns.error('Template-Zuordnung konnte nicht gespeichert werden')
    });
  }

  getRolesForType(typeId: string): any[] {
    if (!typeId) return this.allRoles;
    const type = this.teamTypesList.find(t => t.id === typeId);
    if (!type || !type.role_ids || type.role_ids.length === 0) return this.allRoles;
    return this.allRoles.filter(r => type.role_ids.includes(r.id));
  }

  createTeam() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.busy = true;
    
    const payload = {
      name: this.newTeam.name,
      description: this.newTeam.description,
      team_type_id: this.newTeam.team_type_id,
      members: this.newTeam.members
    };

    const obs = this.newTeam.id
        ? this.hubApi.patchTeam(this.hub.url, this.newTeam.id, payload)
        : this.hubApi.createTeam(this.hub.url, payload);

    obs.subscribe({
      next: () => {
        this.ns.success(this.newTeam.id ? 'Team aktualisiert' : 'Team erstellt');
        this.resetForm();
        this.refresh();
      },
      error: (err) => this.handleTeamError(err, 'Fehler beim Speichern'),
      complete: () => this.busy = false
    });
  }

  setupScrum() {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.busy = true;
    const name = this.newTeam.name?.trim() || undefined;
    this.hubApi.setupScrumTeam(this.hub.url, name).subscribe({
      next: () => {
        this.ns.success('Scrum Team erstellt');
        this.resetForm();
        this.refresh();
      },
      error: (err) => this.handleTeamError(err, 'Scrum Team konnte nicht erstellt werden'),
      complete: () => this.busy = false
    });
  }

  edit(team: any) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    this.newTeam = { ...team };
    if (!this.newTeam.members) {
      this.newTeam.members = [];
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  deleteTeam(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Team wirklich löschen?')) return;
    this.hubApi.deleteTeam(this.hub.url, id).subscribe({
      next: () => { this.ns.success('Team gelöscht'); this.refresh(); }
    });
  }

  activate(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub) return;
    this.hubApi.activateTeam(this.hub.url, id).subscribe({
      next: () => { this.ns.success('Team aktiviert'); this.refresh(); }
    });
  }

  availableAgents(team: any) {
    const memberUrls = (team.members || []).map((m: any) => m.agent_url);
    return this.allAgents.filter(a => !memberUrls.includes(a.url) && a.role !== 'hub');
  }

  addAgentToTeam(team: any, agentUrl: string, roleId: string = '', customTemplateId: string = '') {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!agentUrl || !this.hub) return;
    
    // Finden wir heraus, ob wir das Team im Formular (newTeam) oder ein existierendes Team bearbeiten
    if (team === this.newTeam) {
      if (!this.newTeam.members) this.newTeam.members = [];
      this.newTeam.members.push({ agent_url: agentUrl, role_id: roleId, custom_template_id: customTemplateId });
    } else {
      // Direktes Hinzufügen zu einem existierenden Team über API
      const members = [...(team.members || []), { agent_url: agentUrl, role_id: roleId, custom_template_id: customTemplateId }];
      this.hubApi.patchTeam(this.hub.url, team.id, { members }).subscribe({
        next: () => { this.ns.success('Agent hinzugefügt'); this.refresh(); },
        error: () => this.ns.error('Fehler beim Hinzufügen')
      });
    }
  }

  removeMemberFromForm(index: number) {
    this.newTeam.members.splice(index, 1);
  }

  getAgentNameByUrl(url: string): string {
    return this.allAgents.find(a => a.url === url)?.name || url;
  }

  getTeamTypeName(id: string): string {
    return this.teamTypesList.find(t => t.id === id)?.name || 'Unbekannt';
  }

  getRoleName(id: string): string {
    return this.allRoles.find(r => r.id === id)?.name || 'Keine Rolle';
  }

  getTemplateName(id: string) {
    return this.templates.find(t => t.id === id)?.name || id;
  }

  private handleTeamError(err: any, fallback: string) {
    const code = err?.error?.error;
    const message = err?.error?.message;
    const roleId = err?.error?.role_id;
    const templateId = err?.error?.template_id;
    const hints: Record<string, string> = {
      team_type_not_found: 'Team-Typ nicht gefunden.',
      role_not_found: roleId ? `Rolle nicht gefunden: ${roleId}` : 'Rolle nicht gefunden.',
      invalid_role_for_team_type: roleId ? `Rolle nicht erlaubt: ${roleId}` : 'Rolle nicht f\u00fcr Team-Typ erlaubt.',
      template_not_found: templateId ? `Template nicht gefunden: ${templateId}` : 'Template nicht gefunden.',
      role_id_required: 'Rollen-ID erforderlich.'
    };
    if (code && hints[code]) {
      this.ns.error(hints[code]);
      return;
    }
    if (message) {
      this.ns.error(message);
      return;
    }
    this.ns.error(fallback);
  }
}
