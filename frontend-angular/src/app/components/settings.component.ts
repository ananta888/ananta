import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { ChangePasswordComponent } from './change-password.component';
import { UserManagementComponent } from './user-management.component';
import { MfaSetupComponent } from './mfa-setup.component';

@Component({
  standalone: true,
  selector: 'app-settings',
  imports: [CommonModule, FormsModule, ChangePasswordComponent, UserManagementComponent, MfaSetupComponent],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>System-Einstellungen</h2>
      <div class="row">
        <button (click)="toggleDarkMode()" class="button-outline">
          {{ isDarkMode ? '☀️ Light Mode' : '🌙 Dark Mode' }}
        </button>
        <button (click)="load()" class="button-outline">🔄 Aktualisieren</button>
      </div>
    </div>
    <p class="muted">Konfiguration des Hub-Agenten und globale Parameter.</p>

    <div class="grid cols-2">
      <app-change-password style="margin-bottom: 20px; display: block;"></app-change-password>
      <app-mfa-setup style="margin-bottom: 20px; display: block;"></app-mfa-setup>
    </div>

    <app-user-management *ngIf="isAdmin" style="margin-bottom: 20px; display: block;"></app-user-management>

    <div class="card danger" *ngIf="!hub">
      <p>Kein Hub-Agent konfiguriert. Bitte legen Sie einen Agenten mit der Rolle "hub" fest.</p>
    </div>

    <div class="grid" *ngIf="hub">
      <div class="card" style="border-left: 4px solid #38bdf8;">
        <h3>Hinweis LLM-Konfiguration</h3>
        <p class="muted" style="margin-top: 6px;">Diese Werte werden standardmaessig fuer KI-Funktionen verwendet.</p>
        <div class="grid cols-2">
          <div>
            <div class="muted">Provider</div>
            <div>{{ getEffectiveProvider() }}</div>
          </div>
          <div>
            <div class="muted">Model</div>
            <div>{{ getEffectiveModel() }}</div>
          </div>
          <div>
            <div class="muted">Base URL</div>
            <div>{{ getEffectiveBaseUrl() }}</div>
          </div>
          <div>
            <div class="muted">API Key</div>
            <div>{{ requiresApiKey(getEffectiveProvider()) ? (hasApiKey(getEffectiveProvider()) ? 'ok' : 'missing') : 'not required' }}</div>
          </div>
        </div>
      </div>

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

      <div class="card" *ngIf="llmHistory && llmHistory.length > 0">
        <h3>LMStudio Modell-Historie</h3>
        <p class="muted">Zuletzt verwendete oder verfügbare Modelle von LMStudio.</p>
        <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
          <thead>
            <tr style="text-align: left; border-bottom: 1px solid #ddd;">
              <th style="padding: 8px;">Modell ID</th>
              <th style="padding: 8px;">Zuletzt gesehen</th>
            </tr>
          </thead>
          <tbody>
            <tr *ngFor="let h of llmHistory" style="border-bottom: 1px solid #eee;">
              <td style="padding: 8px; font-family: monospace; font-size: 13px;">{{ h.model || h.id }}</td>
              <td style="padding: 8px; font-size: 13px;">{{ h.last_seen || '-' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="row" style="margin-top: 15px;">
          <button (click)="loadHistory()" class="button-outline">Aktualisieren</button>
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
  llmHistory: any[] = [];
  isAdmin = false;
  isDarkMode = document.body.classList.contains('dark-mode');

  constructor(
    private dir: AgentDirectoryService,
    private api: AgentApiService,
    private ns: NotificationService,
    private auth: UserAuthService
  ) {}

  ngOnInit() {
    this.auth.user$.subscribe(user => {
      this.isAdmin = user?.role === 'admin';
    });
    this.load();
    this.loadHistory();
  }

  toggleDarkMode() {
    this.isDarkMode = !this.isDarkMode;
    if (this.isDarkMode) {
      document.body.classList.add('dark-mode');
      localStorage.setItem('ananta.dark-mode', 'true');
    } else {
      document.body.classList.remove('dark-mode');
      localStorage.setItem('ananta.dark-mode', 'false');
    }
  }

  load() {
    if (!this.hub) {
        this.hub = this.dir.list().find(a => a.role === 'hub');
    }
    this.allAgents = this.dir.list();
    if (!this.hub) return;
    
    this.api.getConfig(this.hub.url).subscribe({
      next: cfg => {
        this.config = cfg;
        this.configRaw = JSON.stringify(cfg, null, 2);
      },
      error: () => this.ns.error('Einstellungen konnten nicht geladen werden')
    });
  }

  loadHistory() {
    if (!this.hub) return;
    this.api.getLlmHistory(this.hub.url).subscribe({
      next: history => {
        this.llmHistory = history || [];
      },
      error: () => console.warn('Konnte LLM Historie nicht laden')
    });
  }

  save() {
    if (!this.hub) return;
    this.api.setConfig(this.hub.url, this.config).subscribe({
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
      this.api.setConfig(this.hub.url, cfg).subscribe({
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

  getEffectiveProvider(): string {
    return (this.config?.default_provider || 'ollama').toLowerCase();
  }

  getEffectiveModel(): string {
    const model = this.config?.default_model;
    return model && String(model).trim().length ? model : '(auto)';
  }

  getEffectiveBaseUrl(): string {
    const provider = this.getEffectiveProvider();
    const llmCfg = this.config?.llm_config || {};
    if (llmCfg?.provider === provider && llmCfg?.base_url) {
      return llmCfg.base_url;
    }
    const providerDefaults: Record<string, string> = {
      ollama: 'http://localhost:11434/api/generate',
      lmstudio: 'http://192.168.56.1:1234/v1',
      openai: 'https://api.openai.com/v1/chat/completions',
      anthropic: 'https://api.anthropic.com/v1/messages'
    };
    const key = `${provider}_url`;
    return this.config?.[key] || providerDefaults[provider] || '(nicht gesetzt)';
  }

  requiresApiKey(provider: string): boolean {
    return provider === 'openai' || provider === 'anthropic';
  }

  hasApiKey(provider: string): boolean {
    const llmCfg = this.config?.llm_config || {};
    if (llmCfg?.provider === provider && llmCfg?.api_key) return true;
    if (provider === 'openai') return Boolean(this.config?.openai_api_key);
    if (provider === 'anthropic') return Boolean(this.config?.anthropic_api_key);
    return false;
  }
}




