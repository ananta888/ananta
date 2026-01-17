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
        <label>Typ <select [(ngModel)]="newTeam.team_type_id">
          <option value="">-- Typ wählen --</option>
          <option *ngFor="let type of teamTypesList" [value]="type.id">{{type.name}}</option>
        </select></label>
        <label>Beschreibung <input [(ngModel)]="newTeam.description" placeholder="Ziele des Teams..."></label>
      </div>

      <div *ngIf="newTeam.name" style="margin-top: 20px; border-top: 1px solid #eee; padding-top: 15px;">
        <h4>Mitglieder & Rollen</h4>
        <div *ngFor="let m of newTeam.members; let i = index" class="row" style="margin-bottom: 8px; align-items: center; gap: 10px;">
          <div style="min-width: 150px;"><strong>{{ getAgentNameByUrl(m.agent_url) }}</strong></div>
          <select [(ngModel)]="m.role_id" style="margin-bottom: 0;">
            <option value="">-- Rolle wählen --</option>
            <option *ngFor="let role of allRoles" [value]="role.id">{{role.name}}</option>
          </select>
          <button (click)="removeMemberFromForm(i)" class="danger" style="padding: 4px 8px;">×</button>
        </div>
        <div *ngIf="!newTeam.members?.length" class="muted">Fügen Sie unten in der Liste Agenten hinzu.</div>
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
             <span class="muted" style="margin-left: 10px;">({{ getTeamTypeName(team.team_type_id) }})</span>
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
             <div *ngFor="let m of team.members" class="agent-chip" style="flex-direction: column; align-items: flex-start;">
                <div class="row" style="width:100%; justify-content: space-between;">
                  <strong>{{ getAgentNameByUrl(m.agent_url) }}</strong>
                </div>
                <div style="font-size: 11px; margin-top: 4px;">
                  <span class="badge" style="background: #007bff; margin-right: 4px;">{{ getRoleName(m.role_id) }}</span>
                </div>
             </div>
             <div *ngIf="!team.members?.length" class="muted" style="font-style: italic; font-size: 0.9em;">Keine Agenten zugeordnet.</div>
          </div>
        </div>

        <div style="margin-top: 15px;">
          <label style="font-size: 12px; display: block; margin-bottom: 4px;">Agent hinzufügen:</label>
          <select #agentSelect (change)="addAgentToTeam(team, agentSelect.value); agentSelect.value=''" style="width: 100%; max-width: 300px;">
            <option value="">-- Agent wählen --</option>
            <option *ngFor="let a of availableAgents(team)" [value]="a.url">
                {{a.name}} ({{a.url}})
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
  teamTypesList: any[] = [];
  allRoles: any[] = [];
  busy = false;
  newTeam: any = { name: '', team_type_id: '', description: '', members: [] };
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

    this.hubApi.listTeamTypes(this.hub.url).subscribe({
      next: r => this.teamTypesList = r,
      error: () => this.ns.error('Team-Typen konnten nicht geladen werden')
    });

    this.hubApi.listTeamRoles(this.hub.url).subscribe({
      next: r => this.allRoles = r,
      error: () => this.ns.error('Rollen konnten nicht geladen werden'),
      complete: () => this.busy = false
    });
  }

  resetForm() {
    this.newTeam = { name: '', team_type_id: '', description: '', members: [] };
  }

  createTeam() {
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
      error: () => this.ns.error('Fehler beim Speichern'),
      complete: () => this.busy = false
    });
  }

  edit(team: any) {
    this.newTeam = { ...team };
    if (!this.newTeam.members) {
      this.newTeam.members = [];
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
    const memberUrls = (team.members || []).map((m: any) => m.agent_url);
    return this.allAgents.filter(a => !memberUrls.includes(a.url) && a.role !== 'hub');
  }

  addAgentToTeam(team: any, agentUrl: string) {
    if (!agentUrl || !this.hub) return;
    
    // Finden wir heraus, ob wir das Team im Formular (newTeam) oder ein existierendes Team bearbeiten
    if (team === this.newTeam) {
      if (!this.newTeam.members) this.newTeam.members = [];
      this.newTeam.members.push({ agent_url: agentUrl, role_id: '' });
    } else {
      // Direktes Hinzufügen zu einem existierenden Team über API
      const members = [...(team.members || []), { agent_url: agentUrl, role_id: '' }];
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
}
