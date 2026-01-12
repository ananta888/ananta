import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { AgentDirectoryService, AgentEntry } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-agent-panel',
  imports: [CommonModule, FormsModule],
  styles: [`
    .tab-btn {
      padding: 8px 16px;
      border: none;
      background: none;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      font-weight: 500;
    }
    .tab-btn.active {
      border-bottom: 2px solid var(--primary-color, #007bff);
      color: var(--primary-color, #007bff);
    }
    .tab-btn:hover:not(.active) {
      background: #f0f0f0;
    }
  `],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Agent Panel – {{agent?.name}}</h2>
      <a *ngIf="agent" [href]="agent.url + '/apidocs'" target="_blank" class="button-outline" style="font-size: 12px; padding: 4px 8px;">API Docs (Swagger)</a>
    </div>
    <p class="muted">{{agent?.url}}</p>

    <div class="row" style="margin-bottom: 16px; border-bottom: 1px solid #ddd;">
      <button class="tab-btn" [class.active]="activeTab === 'interact'" (click)="setTab('interact')">Interaktion</button>
      <button class="tab-btn" [class.active]="activeTab === 'config'" (click)="setTab('config')">Konfiguration</button>
      <button class="tab-btn" [class.active]="activeTab === 'logs'" (click)="setTab('logs')">Logs</button>
      <button class="tab-btn" [class.active]="activeTab === 'system'" (click)="setTab('system')">System</button>
    </div>

    <div class="card grid" *ngIf="activeTab === 'interact'">
      <label>Prompt
        <textarea [(ngModel)]="prompt" rows="6" placeholder="REASON/COMMAND Format"></textarea>
      </label>
      <label>Command (manuell)
        <input [(ngModel)]="command" placeholder="z. B. echo hello" />
      </label>
      <div class="row">
        <button (click)="onPropose()" [disabled]="busy">Vorschlag holen</button>
        <button (click)="onExecute()" [disabled]="busy || !command">Ausführen</button>
        <span class="muted" *ngIf="busy">Bitte warten…</span>
      </div>
      <div *ngIf="reason || command" class="grid">
        <div><strong>Reason:</strong> {{reason}}</div>
        <div><strong>Command:</strong> <code>{{command}}</code></div>
      </div>
      <div *ngIf="execOut" class="card">
        <div><strong>Exit:</strong> {{execExit}}</div>
        <pre style="white-space: pre-wrap">{{execOut}}</pre>
      </div>
    </div>

    <div class="card" *ngIf="activeTab === 'config'">
      <h3>Konfiguration</h3>
      <textarea [(ngModel)]="configJson" rows="12" style="font-family: monospace;"></textarea>
      <div class="row" style="margin-top: 8px;">
        <button (click)="saveConfig()" [disabled]="busy">Speichern</button>
        <button (click)="loadConfig()" class="button-outline" [disabled]="busy">Neu laden</button>
      </div>
    </div>

    <div class="card" *ngIf="activeTab === 'system'">
      <h3>System & Status</h3>
      <div class="grid cols-2">
        <div>
          <h4>Aktionen</h4>
          <button (click)="onRotateToken()" class="danger" [disabled]="busy">Token rotieren</button>
          <p class="muted" style="font-size: 11px; margin-top: 4px;">Generiert einen neuen Agent-Token und ungültig macht den alten.</p>
        </div>
        <div>
          <h4>Metrics</h4>
          <button (click)="loadMetrics()" [disabled]="busy">Metrics laden</button>
        </div>
      </div>
      <div *ngIf="metrics" class="card" style="margin-top: 12px; background: #f9f9f9;">
        <pre style="font-size: 11px; max-height: 300px; overflow: auto;">{{metrics}}</pre>
      </div>
    </div>

    <div class="card" *ngIf="activeTab === 'logs'">
      <h3>Letzte Logs</h3>
      <div class="grid" *ngIf="logs?.length; else noLogs">
        <div *ngFor="let l of logs" class="row" style="justify-content: space-between; border-bottom: 1px solid #eee; padding: 4px 0;">
          <div style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
            <code>{{l.command}}</code>
          </div>
          <span class="muted" style="margin-left: 10px;">RC: {{l.returncode}}</span>
        </div>
      </div>
      <ng-template #noLogs><p class="muted">Keine Logs vorhanden.</p></ng-template>
    </div>
  `
})
export class AgentPanelComponent {
  agent?: AgentEntry;
  activeTab = 'interact';
  prompt = '';
  reason = '';
  command = '';
  execOut = '';
  execExit: any = '';
  busy = false;
  logs: any[] = [];
  configJson = '';
  metrics = '';

  constructor(private route: ActivatedRoute, private dir: AgentDirectoryService, private api: AgentApiService, private ns: NotificationService) {
    const name = this.route.snapshot.paramMap.get('name')!;
    this.agent = this.dir.get(name);
    if (!this.agent) return;
    this.loadLogs();
    this.loadConfig();
  }

  setTab(t: string) { this.activeTab = t; }

  onPropose() {
    if (!this.agent) return;
    this.busy = true;
    this.api.propose(this.agent.url, { prompt: this.prompt }).subscribe({
      next: (r: any) => { this.reason = r?.reason || ''; this.command = r?.command || ''; },
      error: () => this.ns.error('Fehler beim Abrufen des Vorschlags'),
      complete: () => { this.busy = false; }
    });
  }
  onExecute() {
    if (!this.agent || !this.command) return;
    this.busy = true;
    this.api.execute(this.agent.url, { command: this.command }, this.agent.token).subscribe({
      next: (r: any) => { 
        this.execOut = r?.stdout || ''; 
        this.execExit = r?.exit_code; 
        if (this.execExit === 0) this.ns.success('Befehl erfolgreich ausgeführt');
        else this.ns.error(`Befehl fehlgeschlagen (Exit: ${this.execExit})`);
        this.loadLogs(); 
      },
      error: () => { 
        this.execOut = 'Fehler bei Ausführung'; 
        this.ns.error('Fehler bei der Kommunikation mit dem Agenten');
      },
      complete: () => { this.busy = false; }
    });
  }
  loadLogs() {
    if (!this.agent) return;
    this.api.logs(this.agent.url, 50).subscribe({ 
      next: (r: any) => this.logs = r || [],
      error: () => this.ns.error('Logs konnten nicht geladen werden')
    });
  }

  loadConfig() {
    if (!this.agent) return;
    this.api.getConfig(this.agent.url).subscribe({
      next: (cfg) => this.configJson = JSON.stringify(cfg, null, 2),
      error: () => this.ns.error('Konfiguration konnte nicht geladen werden')
    });
  }
  saveConfig() {
    if (!this.agent) return;
    try {
      const cfg = JSON.parse(this.configJson);
      this.busy = true;
      this.api.setConfig(this.agent.url, cfg, this.agent.token).subscribe({
        next: () => this.ns.success('Konfiguration gespeichert'),
        error: () => this.ns.error('Konfiguration konnte nicht gespeichert werden'),
        complete: () => { this.busy = false; }
      });
    } catch(e) {
      this.ns.error('Ungültiges JSON-Format');
    }
  }
  onRotateToken() {
    if (!this.agent || !this.agent.token) return;
    if (!confirm("Token wirklich rotieren? Der aktuelle Token wird sofort ungültig.")) return;
    this.busy = true;
    this.api.rotateToken(this.agent.url, this.agent.token).subscribe({
      next: (r) => {
        if (this.agent) {
          this.agent.token = r.new_token;
          this.dir.upsert(this.agent);
          this.ns.success('Token wurde erfolgreich rotiert');
        }
      },
      error: () => this.ns.error('Token-Rotation fehlgeschlagen'),
      complete: () => this.busy = false
    });
  }
  loadMetrics() {
    if (!this.agent) return;
    this.busy = true;
    this.api.getMetrics(this.agent.url).subscribe({
      next: (m) => this.metrics = m,
      complete: () => this.busy = false
    });
  }
}
