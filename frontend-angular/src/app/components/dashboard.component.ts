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

    <div class="grid cols-3" *ngIf="stats">
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
        <h3>System Status</h3>
        <div class="row" style="align-items: center; gap: 8px;">
          <div class="status-dot" [class.online]="stats.agents.online > 0" [class.offline]="stats.agents.online === 0"></div>
          <strong>{{stats.agents.online > 0 ? 'Betriebsbereit' : 'Eingeschränkt'}}</strong>
        </div>
        <div class="muted" style="font-size: 12px; margin-top: 10px;">
          Hub: {{stats.agent_name}}<br>
          Letztes Update: {{stats.timestamp * 1000 | date:'HH:mm:ss'}}
        </div>
        <div style="margin-top: 15px;">
           <button [routerLink]="['/board']" style="width: 100%;">Zum Task-Board</button>
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
    
    this.hubApi.getStats(this.hub.url, this.hub.token).subscribe({
      next: s => this.stats = s,
      error: e => console.error('Dashboard stats error', e)
    });
  }
}
