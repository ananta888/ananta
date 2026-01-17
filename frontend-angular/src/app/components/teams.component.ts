import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService, AgentEntry } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-teams',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Team-Verwaltung</h2>
      <button (click)="refresh()" [disabled]="busy">🔄 Aktualisieren</button>
    </div>
    
    <div class="card grid" style="margin-bottom: 20px;">
      <h3>Team konfigurieren</h3>
      <div class="grid cols-3">
        <label>Name <input [(ngModel)]="newTeam.name" placeholder="z.B. Scrum Team Alpha"></label>
        <label>Typ <select [(ngModel)]="newTeam.type" (change)="initRoleConfigs()">
          <option *ngFor="let type of teamTypes()" [value]="type">{{type}}</option>
        </select></label>
        <label>Beschreibung <input [(ngModel)]="newTeam.description" placeholder="Ziele des Teams..."></label>
      </div>

      <div *ngIf="newTeam.name" style="margin-top: 20px; border-top: 1px solid #eee; padding-top: 15px;">
        <div class="grid cols-2">
          <div>
            <h4>1. Rollen-Konfiguration</h4>
            <p class="muted">Welches Template soll für welche Rolle in diesem Team gelten?</p>
            <div *ngFor="let role of teamRoles[newTeam.type]" class="row" style="margin-bottom: 8px; align-items: center; gap: 10px;">
              <div style="min-width: 120px;"><strong>{{role}}</strong></div>
              <select [ngModel]="newTeam.role_templates.role_configs[role]" (ngModelChange)="setRoleTemplate(role, $event)" style="margin-bottom: 0;">
                <option value="">-- Template wählen --</option>
                <option *ngFor="let t of templates" [value]="t.id">{{t.name}}</option>
              </select>
            </div>
          </div>
          
          <div>
            <h4>2. Mitglieder & Rollen</h4>
            <p class="muted">Welches Mitglied hat welche Rolle?</p>
            <div *ngFor="let agentName of newTeam.agent_names" class="row" style="margin-bottom: 8px; align-items: center; gap: 10px;">
              <div style="min-width: 120px;"><strong>{{agentName}}</strong></div>
              <select [ngModel]="newTeam.role_templates.member_roles[agentName]" (ngModelChange)="setMemberRole(agentName, $event)" style="margin-bottom: 0;">
                <option value="">-- Rolle wählen --</option>
                <option *ngFor="let role of teamRoles[newTeam.type]" [value]="role">{{role}}</option>
              </select>
            </div>
            <div *ngIf="!newTeam.agent_names?.length" class="muted">Fügen Sie unten in der Liste Agenten hinzu.</div>
          </div>
        </div>
      </div>

      <div class="row" style="margin-top:10px">
        <button (click)="createTeam()" [disabled]="busy || !newTeam.name">Speichern</button>
        <button (click)="resetForm()" class="button-outline">Neu</button>
      </div>
    </div>

    <div class="grid" *ngIf="teams.length">
      <div class="card" *ngFor="let team of teams" [class.active-team]="team.is_active">
        <div class="row" style="justify-content: space-between; align-items: flex-start;">
          <div>
             <span *ngIf="team.is_active" class="badge">AKTIV</span>
             <strong style="font-size: 1.2em;">{{team.name}}</strong> 
             <span class="muted" style="margin-left: 10px;">({{team.type}})</span>
          </div>
          <div class="row">
            <button (click)="edit(team)" class="button-outline" style="padding: 4px 8px; font-size: 12px;">Edit</button>
            <button *ngIf="!team.is_active" (click)="activate(team.id)" class="button-outline" style="padding: 4px 8px; font-size: 12px;">Aktivieren</button>
            <button (click)="deleteTeam(team.id)" class="danger" style="padding: 4px 8px; font-size: 12px;">Löschen</button>
          </div>
        </div>
        <p class="muted" style="margin: 8px 0;">{{team.description}}</p>
        
        <div style="margin-top: 15px; border-top: 1px solid #eee; padding-top: 10px;">
          <h4 style="margin-bottom: 8px;">Agenten im Team:</h4>
          <div class="row wrap">
             <div *ngFor="let name of team.agent_names" class="agent-chip" style="flex-direction: column; align-items: flex-start;">
                <div class="row" style="width:100%; justify-content: space-between;">
                  <strong>{{name}}</strong>
                  <span (click)="removeAgentFromTeam(team, name)" style="cursor:pointer; color:red; margin-left:8px; font-weight:bold;">×</span>
                </div>
                <div *ngIf="getMemberRole(team, name)" style="font-size: 11px; margin-top: 4px;">
                  <span class="badge" style="background: #007bff; margin-right: 4px;">{{getMemberRole(team, name)}}</span>
                  <span class="muted">{{getMemberTemplateName(team, name)}}</span>
                </div>
             </div>
             <div *ngIf="!team.agent_names?.length" class="muted" style="font-style: italic; font-size: 0.9em;">Keine Agenten zugeordnet.</div>
          </div>
        </div>

        <div style="margin-top: 15px;">
          <label style="font-size: 12px; display: block; margin-bottom: 4px;">Agent hinzufügen / verschieben:</label>
          <select #agentSelect (change)="addAgentToTeam(team, agentSelect.value); agentSelect.value=''" style="width: 100%; max-width: 300px;">
            <option value="">-- Agent wählen --</option>
            <option *ngFor="let a of availableAgents(team)" [value]="a.name">
                {{a.name}} {{ getAgentTeamInfo(a.name) }}
            </option>
          </select>
        </div>
      </div>
    </div>

    <div *ngIf="!teams.length && !busy" class="card muted" style="text-align: center; padding: 40px;">
        Keine Teams vorhanden. Legen Sie oben ein neues Team an.
    </div>

    <style>
      .active-team { border: 2px solid #28a745 !important; background: #f8fff9; }
      .badge { background: #28a745; color: white; padding: 2px 6px; border-radius: 4px; font-size: 10px; margin-right: 8px; vertical-align: middle; font-weight: bold; }
      .agent-chip { background: #fff; border: 1px solid #ccc; padding: 4px 12px; border-radius: 16px; font-size: 13px; margin-right: 8px; margin-bottom: 8px; display: flex; align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
      .wrap { flex-wrap: wrap; }
      .muted { color: #666; font-size: 0.9em; }
    </style>
  `
})
export class TeamsComponent implements OnInit {
  teams: any[] = [];
  templates: any[] = [];
  teamRoles: any = {};
  busy = false;
  newTeam: any = { name: '', type: 'Scrum', description: '', agent_names: [], role_templates: { role_configs: {}, member_roles: {} } };
  hub = this.dir.list().find(a => a.role === 'hub');
  teamAgent: any;
  allAgents = this.dir.list();

  constructor(
    private dir: AgentDirectoryService, 
    private hubApi: HubApiService, 
    private agentApi: AgentApiService,
    private ns: NotificationService
  ) {}

  ngOnInit() {
    this.refresh();
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
      next: r => { this.teams = r; this.allAgents = this.dir.list(); },
      error: () => this.ns.error('Teams konnten nicht geladen werden')
    });

    this.hubApi.listTemplates(this.hub.url).subscribe({
      next: r => this.templates = r,
      error: () => this.ns.error('Templates konnten nicht geladen werden')
    });

    this.hubApi.listTeamRoles(this.hub.url).subscribe({
      next: r => this.teamRoles = r,
      error: () => this.ns.error('Team-Rollen konnten nicht geladen werden'),
      complete: () => this.busy = false
    });
  }

  resetForm() {
    this.newTeam = { name: '', type: 'Scrum', description: '', agent_names: [], role_templates: { role_configs: {}, member_roles: {} } };
  }

  createTeam() {
    if (!this.hub) return;
    this.busy = true;
    const obs = this.newTeam.id
        ? this.hubApi.patchTeam(this.hub.url, this.newTeam.id, this.newTeam)
        : this.hubApi.createTeam(this.hub.url, this.newTeam);

    obs.subscribe({
      next: () => {
        this.ns.success(this.newTeam.id ? 'Team aktualisiert' : 'Team erstellt');
        this.resetForm();
        this.refresh();
      },
      error: () => this.ns.error('Fehler beim Speichern'),
      complete: () => this.busy = false
    });
  }

  edit(team: any) {
    this.newTeam = { ...team };
    if (!this.newTeam.role_templates || !this.newTeam.role_templates.role_configs) {
      this.newTeam.role_templates = { role_configs: {}, member_roles: {} };
      // Versuche alte Struktur zu migrieren falls vorhanden
      if (team.role_templates && !team.role_templates.role_configs) {
        Object.keys(team.role_templates).forEach(agent => {
          const entry = team.role_templates[agent];
          if (entry.role) {
            this.newTeam.role_templates.member_roles[agent] = entry.role;
            if (entry.template_id) this.newTeam.role_templates.role_configs[entry.role] = entry.template_id;
          }
        });
      }
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  deleteTeam(id: string) {
    if (!this.hub || !confirm('Team wirklich löschen?')) return;
    this.hubApi.deleteTeam(this.hub.url, id).subscribe({
      next: () => { this.ns.success('Team gelöscht'); this.refresh(); }
    });
  }

  activate(id: string) {
    if (!this.hub) return;
    this.hubApi.activateTeam(this.hub.url, id).subscribe({
      next: () => { this.ns.success('Team aktiviert'); this.refresh(); }
    });
  }

  availableAgents(team: any) {
    return this.allAgents.filter(a => !team.agent_names?.includes(a.name) && a.role !== 'hub');
  }

  getAgentTeamInfo(agentName: string): string {
    const team = this.teams.find(t => t.agent_names?.includes(agentName));
    return team ? `(aktuell in: ${team.name})` : '';
  }

  addAgentToTeam(team: any, agentName: string) {
    if (!agentName || !this.hub) return;
    const otherTeam = this.teams.find(t => t.id !== team.id && t.agent_names?.includes(agentName));
    if (otherTeam) {
       const cleanedNames = otherTeam.agent_names.filter((n: string) => n !== agentName);
       this.hubApi.patchTeam(this.hub.url, otherTeam.id, { agent_names: cleanedNames }).subscribe({
         next: () => this.doAddAgent(team, agentName),
         error: () => this.ns.error('Fehler beim Verschieben')
       });
    } else {
       this.doAddAgent(team, agentName);
    }
  }

  private doAddAgent(team: any, agentName: string) {
    if (!this.hub) return;
    const updatedAgentNames = [...(team.agent_names || []), agentName];
    let updatedRoleTemplates = { ...(team.role_templates || { role_configs: {}, member_roles: {} }) };
    
    if (!updatedRoleTemplates.member_roles) {
      updatedRoleTemplates = { role_configs: {}, member_roles: {} };
    }

    // Automatisch Rolle zuweisen falls noch Platz in den Standardrollen
    if (this.teamRoles[team.type]) {
      const usedRoles = Object.values(updatedRoleTemplates.member_roles);
      const nextRole = this.teamRoles[team.type].find((r: string) => !usedRoles.includes(r));
      if (nextRole) {
        updatedRoleTemplates.member_roles[agentName] = nextRole;
      }
    }

    this.hubApi.patchTeam(this.hub.url, team.id, { 
      agent_names: updatedAgentNames,
      role_templates: updatedRoleTemplates
    }).subscribe({
      next: () => { this.ns.success(`${agentName} hinzugefügt`); this.refresh(); },
      error: () => this.ns.error('Fehler beim Hinzufügen')
    });
  }

  removeAgentFromTeam(team: any, agentName: string) {
    if (!this.hub) return;
    const updatedAgentNames = team.agent_names.filter((n: string) => n !== agentName);
    const updatedRoleTemplates = { ...(team.role_templates || { role_configs: {}, member_roles: {} }) };
    if (updatedRoleTemplates.member_roles) {
      delete updatedRoleTemplates.member_roles[agentName];
    }
    
    this.hubApi.patchTeam(this.hub.url, team.id, { 
      agent_names: updatedAgentNames,
      role_templates: updatedRoleTemplates 
    }).subscribe({
      next: () => { this.ns.success(`${agentName} entfernt`); this.refresh(); },
      error: () => this.ns.error('Fehler beim Entfernen')
    });
  }

  teamTypes() {
    return Object.keys(this.teamRoles);
  }

  initRoleConfigs() {
    const roles = this.teamRoles[this.newTeam.type] || [];
    roles.forEach((role: string) => {
      if (!this.newTeam.role_templates.role_configs[role]) {
        const tpl = this.templates.find(t => t.name === role);
        if (tpl) this.newTeam.role_templates.role_configs[role] = tpl.id;
      }
    });
  }

  setRoleTemplate(role: string, templateId: string) {
    this.newTeam.role_templates.role_configs[role] = templateId;
  }

  setMemberRole(agentName: string, role: string) {
    this.newTeam.role_templates.member_roles[agentName] = role;
  }

  getMemberRole(team: any, agentName: string): string {
    return team.role_templates?.member_roles?.[agentName] || '';
  }

  getMemberTemplateName(team: any, agentName: string): string {
    const role = this.getMemberRole(team, agentName);
    if (!role) return '';
    const templateId = team.role_templates?.role_configs?.[role];
    if (!templateId) return 'Kein Template';
    return this.templates.find(t => t.id === templateId)?.name || templateId;
  }

  getTemplateName(id: string) {
    return this.templates.find(t => t.id === id)?.name || id;
  }
}
