import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';
import { ChangePasswordComponent } from './change-password.component';

@Component({
  standalone: true,
  selector: 'app-settings',
  imports: [CommonModule, FormsModule, ChangePasswordComponent],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>System-Einstellungen</h2>
      <button (click)="load()" class="button-outline">🔄 Aktualisieren</button>
    </div>
    <p class="muted">Konfiguration des Hub-Agenten und globale Parameter.</p>

    <app-change-password style="margin-bottom: 20px; display: block;"></app-change-password>

    <div class="card danger" *ngIf="!hub">
      <p>Kein Hub-Agent konfiguriert. Bitte legen Sie einen Agenten mit der Rolle "hub" fest.</p>
    </div>

    <div class="grid" *ngIf="hub">
      <div class="card">
        <h3>KI-Unterstützung</h3>
        <p class="muted">Wählen Sie aus, welche Agenten für die KI-Unterstützung im Frontend verwendet werden sollen.</p>
        <div class="grid cols-2">
          <label>Agent für Templates
            <select [(ngModel)]="config.template_agent_name">
              <option [ngValue]="undefined">Hub (Standard)</option>
              <option *ngFor="let a of allAgents" [value]="a.name">{{a.name}} ({{a.role}})</option>
            </select>
          </label>
          <label>Agent für Team-Beratung
            <select [(ngModel)]="config.team_agent_name">
              <option [ngValue]="undefined">Hub (Standard)</option>
              <option *ngFor="let a of allAgents" [value]="a.name">{{a.name}} ({{a.role}})</option>
            </select>
          </label>
        </div>
        <div class="row" style="margin-top: 15px;">
          <button (click)="save()">Speichern</button>
        </div>
      </div>

      <div class="card">
        <h3>Hub LLM Defaults</h3>
        <div class="grid cols-2">
          <label>Default Provider
            <select [(ngModel)]="config.default_provider">
              <option value="ollama">Ollama</option>
              <option value="lmstudio">LMStudio</option>
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </label>
          <label>Default Model
            <input [(ngModel)]="config.default_model" placeholder="z.B. llama3">
          </label>
        </div>
        
        <div class="grid cols-2" style="margin-top: 15px;">
           <label>OpenAI URL
             <input [(ngModel)]="config.openai_url">
           </label>
           <label>Anthropic URL
             <input [(ngModel)]="config.anthropic_url">
           </label>
        </div>

        <div class="row" style="margin-top: 15px;">
          <button (click)="save()">Speichern</button>
        </div>
      </div>

      <div class="card">
        <h3>System Parameter</h3>
        <div class="grid cols-2">
          <label>Log Level
            <select [(ngModel)]="config.log_level">
              <option value="DEBUG">DEBUG</option>
              <option value="INFO">INFO</option>
              <option value="WARNING">WARNING</option>
              <option value="ERROR">ERROR</option>
            </select>
          </label>
          <label>Agent Offline Timeout (s)
            <input type="number" [(ngModel)]="config.agent_offline_timeout">
          </label>
        </div>
        
        <div class="grid cols-2" style="margin-top: 15px;">
           <label>HTTP Timeout (s)
             <input type="number" [(ngModel)]="config.http_timeout">
           </label>
           <label>Command Timeout (s)
             <input type="number" [(ngModel)]="config.command_timeout">
           </label>
        </div>

        <div class="row" style="margin-top: 15px;">
          <button (click)="save()">Speichern</button>
        </div>
      </div>

      <div class="card">
        <h3>Roh-Konfiguration (Hub)</h3>
        <p class="muted" style="font-size: 12px;">Vorsicht: Direkte Bearbeitung der config.json des Hubs.</p>
        <textarea [(ngModel)]="configRaw" rows="10" style="font-family: monospace; width: 100%;"></textarea>
        <div class="row" style="margin-top: 8px;">
          <button (click)="saveRaw()" class="button-outline">Roh-Daten Speichern</button>
        </div>
      </div>
    </div>
  `
})
export class SettingsComponent implements OnInit {
  hub = this.dir.list().find(a => a.role === 'hub');
  allAgents = this.dir.list();
  config: any = {};
  configRaw = '';

  constructor(
    private dir: AgentDirectoryService,
    private api: AgentApiService,
    private ns: NotificationService
  ) {}

  ngOnInit() {
    this.load();
  }

  load() {
    if (!this.hub) {
        this.hub = this.dir.list().find(a => a.role === 'hub');
    }
    this.allAgents = this.dir.list();
    if (!this.hub) return;
    
    this.api.getConfig(this.hub.url, this.hub.token).subscribe({
      next: cfg => {
        this.config = cfg;
        this.configRaw = JSON.stringify(cfg, null, 2);
      },
      error: () => this.ns.error('Einstellungen konnten nicht geladen werden')
    });
  }

  save() {
    if (!this.hub) return;
    this.api.setConfig(this.hub.url, this.config, this.hub.token).subscribe({
      next: () => {
        this.ns.success('Einstellungen gespeichert');
        this.load();
      },
      error: () => this.ns.error('Speichern fehlgeschlagen')
    });
  }

  saveRaw() {
    if (!this.hub) return;
    try {
      const cfg = JSON.parse(this.configRaw);
      this.api.setConfig(this.hub.url, cfg, this.hub.token).subscribe({
        next: () => {
          this.ns.success('Roh-Konfiguration gespeichert');
          this.load();
        },
        error: () => this.ns.error('Speichern fehlgeschlagen')
      });
    } catch (e) {
      this.ns.error('Ungültiges JSON');
    }
  }
}
