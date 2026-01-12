import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { interval, Subscription } from 'rxjs';

@Component({
  standalone: true,
  selector: 'app-dashboard',
  imports: [CommonModule, RouterLink],
  template: `
    <h2>System Dashboard</h2>
    <p class="muted">Zentrale Übersicht über Agenten und Tasks.</p>
    <div class="grid cols-5" *ngIf="stats">
      <div class="card">
        <h3>Agenten</h3>
        <div class="row" style="justify-content: space-between;">
          <span>Gesamt:</span>
          <strong>{{stats.agents.total}}</strong>
        </div>
        <div class="row" style="justify-content: space-between;">
          <span>Online:</span>
          <strong class="success">{{stats.agents.online}}</strong>
        </div>
        <div class="row" style="justify-content: space-between;">
          <span>Offline:</span>
          <strong class="danger">{{stats.agents.offline}}</strong>
        </div>
      </div>

      <div class="card">
        <h3>Tasks</h3>
        <div class="row" style="justify-content: space-between;">
          <span>Gesamt:</span>
          <strong>{{stats.tasks.total}}</strong>
        </div>
        <div class="row" style="justify-content: space-between;">
          <span>Abgeschlossen:</span>
          <strong class="success">{{stats.tasks.completed}}</strong>
        </div>
        <div class="row" style="justify-content: space-between;">
          <span>Fehlgeschlagen:</span>
          <strong class="danger">{{stats.tasks.failed}}</strong>
        </div>
        <div class="row" style="justify-content: space-between;">
          <span>In Arbeit:</span>
          <strong>{{stats.tasks.in_progress}}</strong>
        </div>
      </div>

      <div class="card">
        <h3>Shell Pool</h3>
        <div class="row" style="justify-content: space-between;">
          <span>Gesamt:</span>
          <strong>{{stats.shell_pool?.total || 0}}</strong>
        </div>
        <div class="row" style="justify-content: space-between;">
          <span>Frei:</span>
          <strong class="success">{{stats.shell_pool?.free || 0}}</strong>
        </div>
        <div class="row" style="justify-content: space-between;">
          <span>Belegt:</span>
          <strong [class.danger]="stats.shell_pool?.busy > 0">{{stats.shell_pool?.busy || 0}}</strong>
        </div>
      </div>

      <div class="card" *ngIf="stats.resources">
        <h3>Ressourcen</h3>
        <div class="row" style="justify-content: space-between;">
          <span>CPU:</span>
          <strong>{{stats.resources.cpu_percent | number:'1.1-1'}}%</strong>
        </div>
        <div class="row" style="justify-content: space-between;">
          <span>RAM:</span>
          <strong>{{stats.resources.ram_bytes / 1024 / 1024 | number:'1.0-0'}} MB</strong>
        </div>
        <div style="margin-top: 8px; background: #eee; height: 4px; border-radius: 2px; overflow: hidden;">
           <div [style.width.%]="stats.resources.cpu_percent" [class.bg-danger]="stats.resources.cpu_percent > 80" [class.bg-success]="stats.resources.cpu_percent <= 80" style="height: 100%;"></div>
        </div>
      </div>

      <div class="card">
        <h3>System Status</h3>
        <div class="row" style="align-items: center; gap: 8px;">
          <div class="status-dot" [class.online]="stats.agents.online > 0" [class.offline]="stats.agents.online === 0"></div>
          <strong>{{stats.agents.online > 0 ? 'Betriebsbereit' : 'Eingeschränkt'}}</strong>
        </div>
        <div class="muted" style="font-size: 12px; margin-top: 10px;" *ngIf="activeTeam">
           Aktives Team: <strong>{{activeTeam.name}}</strong> ({{activeTeam.agent_names?.length || 0}} Agenten)
        </div>
        <div class="muted" style="font-size: 12px; margin-top: 10px;" *ngIf="!activeTeam">
           Kein Team aktiv.
        </div>
        <div class="muted" style="font-size: 11px; margin-top: 5px;">
          Hub: {{stats.agent_name}}<br>
          Letztes Update: {{stats.timestamp * 1000 | date:'HH:mm:ss'}}
        </div>
        <div style="margin-top: 15px;">
           <button [routerLink]="['/board']" style="width: 100%;">Zum Task-Board</button>
        </div>
      </div>
    </div>

    <div class="card" *ngIf="history.length > 1">
      <h3>Task-Erfolgsrate über die Zeit</h3>
      <div style="height: 150px; width: 100%; border-bottom: 1px solid #ccc; border-left: 1px solid #ccc; position: relative; margin-top: 20px;">
        <svg width="100%" height="100%" viewBox="0 0 1000 100" preserveAspectRatio="none">
          <!-- Erfolgslinie (grün) -->
          <polyline
            fill="none"
            stroke="#28a745"
            stroke-width="2"
            [attr.points]="getPoints('completed')"
          />
          <!-- Fehlerlinie (rot) -->
          <polyline
            fill="none"
            stroke="#dc3545"
            stroke-width="2"
            [attr.points]="getPoints('failed')"
          />
        </svg>
        <div style="display: flex; justify-content: space-between; font-size: 10px; margin-top: 5px;">
           <span>Vor {{history.length}} Minuten</span>
           <span>Jetzt</span>
        </div>
        <div style="margin-top: 10px; display: flex; gap: 20px; font-size: 12px;">
           <span style="color: #28a745">● Abgeschlossen</span>
           <span style="color: #dc3545">● Fehlgeschlagen</span>
        </div>
      </div>
    </div>

    <div class="card" *ngIf="agentsList.length > 0">
      <h3>Agenten Status</h3>
      <div class="grid cols-4">
        <div *ngFor="let agent of agentsList" style="padding: 8px; border: 1px solid #eee; border-radius: 4px;">
          <div class="row" style="gap: 8px; align-items: center;">
            <div class="status-dot" [class.online]="agent.status === 'online'" [class.offline]="agent.status !== 'online'"></div>
            <span style="font-weight: 500;">{{agent.name}}</span>
            <span class="muted" style="font-size: 11px;">{{agent.role}}</span>
          </div>
          <div *ngIf="agent.resources" class="muted" style="font-size: 11px; margin-top: 5px; display: flex; justify-content: space-between;">
            <span>CPU: {{agent.resources.cpu_percent | number:'1.0-1'}}%</span>
            <span>RAM: {{agent.resources.ram_bytes / 1024 / 1024 | number:'1.0-0'}} MB</span>
          </div>
        </div>
      </div>
    </div>

    <div class="card" *ngIf="!stats && hub">
      <p>Lade Statistiken von Hub ({{hub.url}})...</p>
    </div>

    <div class="card danger" *ngIf="!hub">
      <p>Kein Hub-Agent konfiguriert. Bitte fügen Sie einen Agenten mit der Rolle "hub" hinzu.</p>
      <button [routerLink]="['/agents']">Agenten verwalten</button>
    </div>
  `
})
export class DashboardComponent implements OnInit, OnDestroy {
  hub = this.dir.list().find(a => a.role === 'hub');
  stats: any;
  history: any[] = [];
  agentsList: any[] = [];
  activeTeam: any;
  private sub?: Subscription;

  constructor(private dir: AgentDirectoryService, private hubApi: HubApiService) {}

  ngOnInit() {
    this.refresh();
    this.sub = interval(10000).subscribe(() => this.refresh());
  }

  ngOnDestroy() {
    this.sub?.unsubscribe();
  }

  refresh() {
    if (!this.hub) {
      this.hub = this.dir.list().find(a => a.role === 'hub');
    }
    if (!this.hub) return;
    
    this.hubApi.getStats(this.hub.url).subscribe({
      next: s => this.stats = s,
      error: e => console.error('Dashboard stats error', e)
    });

    this.hubApi.getStatsHistory(this.hub.url).subscribe({
      next: h => this.history = h,
      error: e => console.error('Dashboard history error', e)
    });

    this.hubApi.listTeams(this.hub.url).subscribe({
      next: teams => this.activeTeam = teams.find(t => t.is_active),
      error: e => console.error('Dashboard teams error', e)
    });

    this.hubApi.listAgents(this.hub.url).subscribe({
      next: agents => {
        this.agentsList = Object.entries(agents).map(([name, info]: [string, any]) => ({
          name,
          ...info
        }));
      },
      error: e => console.error('Dashboard agents list error', e)
    });
  }

  getPoints(type: 'completed' | 'failed'): string {
    if (this.history.length < 2) return '';
    
    const maxVal = Math.max(...this.history.map(h => h.tasks.total), 1);
    const stepX = 1000 / (this.history.length - 1);
    
    return this.history.map((h, i) => {
      const val = h.tasks[type] || 0;
      const x = i * stepX;
      const y = 100 - (val / maxVal * 100);
      return `${x},${y}`;
    }).join(' ');
  }
}
