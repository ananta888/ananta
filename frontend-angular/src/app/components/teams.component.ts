import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService, AgentEntry } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
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
      <h3>Neues Team erstellen</h3>
      <div class="grid cols-3">
        <label>Name <input [(ngModel)]="newTeam.name" placeholder="z.B. Scrum Team Alpha"></label>
        <label>Typ <input [(ngModel)]="newTeam.type" placeholder="Scrum, Kanban, etc."></label>
        <label>Beschreibung <input [(ngModel)]="newTeam.description" placeholder="Optional"></label>
      </div>
      <div class="row" style="margin-top:10px">
        <button (click)="createTeam()" [disabled]="busy || !newTeam.name">Team anlegen</button>
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
            <button *ngIf="!team.is_active" (click)="activate(team.id)" class="button-outline" style="padding: 4px 8px; font-size: 12px;">Aktivieren</button>
            <button (click)="deleteTeam(team.id)" class="danger" style="padding: 4px 8px; font-size: 12px;">Löschen</button>
          </div>
        </div>
        <p class="muted" style="margin: 8px 0;">{{team.description}}</p>
        
        <div style="margin-top: 15px; border-top: 1px solid #eee; padding-top: 10px;">
          <h4 style="margin-bottom: 8px;">Agenten im Team:</h4>
          <div class="row wrap">
             <div *ngFor="let name of team.agent_names" class="agent-chip">
                {{name}} 
                <span (click)="removeAgentFromTeam(team, name)" style="cursor:pointer; color:red; margin-left:8px; font-weight:bold;">×</span>
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
export class TeamsComponent {
  teams: any[] = [];
  busy = false;
  newTeam = { name: '', type: 'Scrum', description: '' };
  hub = this.dir.list().find(a => a.role === 'hub');
  allAgents = this.dir.list();

  constructor(private dir: AgentDirectoryService, private hubApi: HubApiService, private ns: NotificationService) {
    this.refresh();
  }

  refresh() {
    if (!this.hub) return;
    this.busy = true;
    this.hubApi.listTeams(this.hub.url).subscribe({
      next: r => { this.teams = r; this.allAgents = this.dir.list(); },
      error: () => this.ns.error('Teams konnten nicht geladen werden'),
      complete: () => this.busy = false
    });
  }

  createTeam() {
    if (!this.hub) return;
    this.busy = true;
    this.hubApi.createTeam(this.hub.url, this.newTeam, this.hub.token).subscribe({
      next: () => {
        this.ns.success('Team erstellt');
        this.newTeam = { name: '', type: 'Scrum', description: '' };
        this.refresh();
      },
      error: () => this.ns.error('Fehler beim Erstellen'),
      complete: () => this.busy = false
    });
  }

  deleteTeam(id: string) {
    if (!this.hub || !confirm('Team wirklich löschen?')) return;
    this.hubApi.deleteTeam(this.hub.url, id, this.hub.token).subscribe({
      next: () => { this.ns.success('Team gelöscht'); this.refresh(); }
    });
  }

  activate(id: string) {
    if (!this.hub) return;
    this.hubApi.activateTeam(this.hub.url, id, this.hub.token).subscribe({
      next: () => { this.ns.success('Team aktiviert'); this.refresh(); }
    });
  }

  availableAgents(team: any) {
    // Alle außer Hub, die noch nicht in DIESEM Team sind
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
       // Verschieben
       const cleanedNames = otherTeam.agent_names.filter((n: string) => n !== agentName);
       this.hubApi.patchTeam(this.hub.url, otherTeam.id, { agent_names: cleanedNames }, this.hub.token).subscribe({
         next: () => this.doAddAgent(team, agentName),
         error: () => this.ns.error('Fehler beim Entfernen aus altem Team')
       });
    } else {
       this.doAddAgent(team, agentName);
    }
  }

  private doAddAgent(team: any, agentName: string) {
    const updatedAgentNames = [...(team.agent_names || []), agentName];
    this.hubApi.patchTeam(this.hub.url, team.id, { agent_names: updatedAgentNames }, this.hub.token).subscribe({
      next: () => {
          this.ns.success(`${agentName} zum Team ${team.name} hinzugefügt`);
          this.refresh();
      },
      error: () => this.ns.error('Fehler beim Hinzufügen zum Team')
    });
  }

  removeAgentFromTeam(team: any, agentName: string) {
    if (!this.hub) return;
    const updatedAgentNames = team.agent_names.filter((n: string) => n !== agentName);
    this.hubApi.patchTeam(this.hub.url, team.id, { agent_names: updatedAgentNames }, this.hub.token).subscribe({
      next: () => this.refresh(),
      error: () => this.ns.error('Fehler beim Entfernen des Agenten')
    });
  }
}
