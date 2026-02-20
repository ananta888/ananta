import { Component, OnInit, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { ChangePasswordComponent } from './change-password.component';
import { UserManagementComponent } from './user-management.component';
import { MfaSetupComponent } from './mfa-setup.component';

@Component({
  standalone: true,
  selector: 'app-settings',
  imports: [FormsModule, ChangePasswordComponent, UserManagementComponent, MfaSetupComponent],
  template: `
    <div class="row flex-between">
      <h2>System-Einstellungen</h2>
      <div class="row gap-sm">
        <button (click)="toggleDarkMode()" class="button-outline">
          {{ isDarkMode ? '☀️ Light Mode' : '🌙 Dark Mode' }}
        </button>
        <button (click)="load()" class="button-outline">🔄 Aktualisieren</button>
      </div>
    </div>
    <p class="muted">Konfiguration des Hub-Agenten und globale Parameter.</p>

    <div class="row gap-sm flex-wrap mb-md">
      <button class="button-outline" [class.active-toggle]="selectedSection==='account'" (click)="setSection('account')">Account</button>
      <button class="button-outline" [class.active-toggle]="selectedSection==='llm'" (click)="setSection('llm')">LLM & AI</button>
      <button class="button-outline" [class.active-toggle]="selectedSection==='quality'" (click)="setSection('quality')">Quality Gates</button>
      <button class="button-outline" [class.active-toggle]="selectedSection==='system'" (click)="setSection('system')">System</button>
    </div>
    
    @if (selectedSection === 'account') {
      <div class="grid cols-2">
        <app-change-password class="block mb-lg"></app-change-password>
        <app-mfa-setup class="block mb-lg"></app-mfa-setup>
      </div>

      @if (isAdmin) {
        <app-user-management class="block mb-lg"></app-user-management>
      }
    }
    
    @if (!hub) {
      <div class="card danger">
        <p>Kein Hub-Agent konfiguriert. Bitte legen Sie einen Agenten mit der Rolle "hub" fest.</p>
      </div>
    }
    
    @if (hub) {
      <div class="grid">
        @if (selectedSection === 'llm') {
        <div class="card card-info">
          <h3>Hinweis LLM-Konfiguration</h3>
          <p class="muted mt-sm">Diese Werte werden standardmaessig fuer KI-Funktionen verwendet.</p>
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
                @for (a of allAgents; track a) {
                  <option [value]="a.name">{{a.name}} ({{a.role}})</option>
                }
              </select>
            </label>
            <label>Agent für Team-Beratung
              <select [(ngModel)]="config.team_agent_name">
                <option [ngValue]="undefined">Hub (Standard)</option>
                @for (a of allAgents; track a) {
                  <option [value]="a.name">{{a.name}} ({{a.role}})</option>
                }
              </select>
            </label>
          </div>
          <div class="row mt-lg">
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'llm') {
        <div class="card">
          <h3>Hub LLM Defaults</h3>
          <div class="grid cols-2">
            <label>Default Provider
              <select [(ngModel)]="config.default_provider" (ngModelChange)="ensureProviderModelConsistency()">
                @for (p of getCatalogProviders(); track p.id) {
                  <option [value]="p.id">
                    {{ p.id }}{{ p.available ? '' : ' (offline)' }}{{ p.model_count ? ' [' + p.model_count + ']' : '' }}
                  </option>
                }
              </select>
            </label>
            <label>Default Model
              <select [(ngModel)]="config.default_model">
                @for (m of getCatalogModels(getEffectiveProvider()); track m.id) {
                  <option [value]="m.id">{{ m.display_name }}{{ m.context_length ? ' (ctx ' + m.context_length + ')' : '' }}</option>
                }
                @if ((config?.default_model || '').trim() && !isCurrentModelInCatalog()) {
                  <option [value]="config.default_model">{{ config.default_model }} (custom)</option>
                }
              </select>
            </label>
          </div>
          <div class="grid cols-2 mt-lg">
            <label>OpenAI URL
              <input [(ngModel)]="config.openai_url">
            </label>
            <label>Anthropic URL
              <input [(ngModel)]="config.anthropic_url">
            </label>
          </div>
          <div class="row mt-lg">
            <button (click)="save()">Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'llm') {
        <div class="card">
          <h3>Benchmark Konfiguration</h3>
          <p class="muted">Aktive Retention- und Fallback-Regeln fuer Modell-Benchmarkdaten.</p>
          <div class="grid cols-2">
            <label>
              Retention max_days
              <input type="number" min="1" max="3650" [(ngModel)]="benchmarkRetentionDays" [class.input-error]="benchmarkRetentionDays < 1 || benchmarkRetentionDays > 3650" />
              @if (benchmarkRetentionDays < 1 || benchmarkRetentionDays > 3650) {
                <span class="error-text">Wert muss zwischen 1 und 3650 liegen</span>
              }
            </label>
            <label>
              Retention max_samples
              <input type="number" min="50" max="50000" [(ngModel)]="benchmarkRetentionSamples" [class.input-error]="benchmarkRetentionSamples < 50 || benchmarkRetentionSamples > 50000" />
              @if (benchmarkRetentionSamples < 50 || benchmarkRetentionSamples > 50000) {
                <span class="error-text">Wert muss zwischen 50 und 50000 liegen</span>
              }
            </label>
            <label class="col-span-full">
              Provider precedence (Komma-getrennt)
              <input [(ngModel)]="benchmarkProviderOrderTextValue" placeholder="proposal_backend, routing_effective_backend, llm_config_provider, default_provider, provider" />
            </label>
            <label class="col-span-full">
              Model precedence (Komma-getrennt)
              <input [(ngModel)]="benchmarkModelOrderTextValue" placeholder="proposal_model, llm_config_model, default_model, model" />
            </label>
          </div>
          @if (benchmarkValidationError) {
            <div class="danger font-sm mt-sm">{{ benchmarkValidationError }}</div>
          }
          @if (benchmarkConfig) {
            <details class="mt-md">
              <summary style="cursor: pointer;">Aktive Defaults anzeigen</summary>
              <pre class="preformatted">{{ benchmarkConfig?.defaults | json }}</pre>
            </details>
          } @else {
            <div class="muted mt-sm">Keine Benchmark-Config verfuegbar.</div>
          }
          <div class="muted font-sm mt-sm">
            Vorschau Provider: <span class="font-mono">{{ benchmarkProviderOrderText() }}</span><br />
            Vorschau Model: <span class="font-mono">{{ benchmarkModelOrderText() }}</span>
          </div>
          <div class="row mt-md gap-sm">
            <button (click)="saveBenchmarkConfig()" [disabled]="benchmarkRetentionDays < 1 || benchmarkRetentionDays > 3650 || benchmarkRetentionSamples < 50 || benchmarkRetentionSamples > 50000">Speichern</button>
            <button class="button-outline" (click)="loadBenchmarkConfig()">Aktualisieren</button>
          </div>
          @if (benchmarkConfig) {
            <details class="mt-md">
              <summary style="cursor: pointer;">Rohdaten anzeigen</summary>
              <pre class="preformatted">{{ benchmarkConfig | json }}</pre>
            </details>
          }
        </div>
        }
        @if (selectedSection === 'system') {
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
              <input type="number" [(ngModel)]="config.agent_offline_timeout" min="10" [class.input-error]="config.agent_offline_timeout < 10">
              @if (config.agent_offline_timeout < 10) {
                <span class="error-text">Mindestens 10 Sekunden</span>
              }
            </label>
          </div>
          <div class="grid cols-2 mt-lg">
            <label>HTTP Timeout (s)
              <input type="number" [(ngModel)]="config.http_timeout" min="1" [class.input-error]="config.http_timeout < 1">
              @if (config.http_timeout < 1) {
                <span class="error-text">Mindestens 1 Sekunde</span>
              }
            </label>
            <label>Command Timeout (s)
              <input type="number" [(ngModel)]="config.command_timeout" min="1" [class.input-error]="config.command_timeout < 1">
              @if (config.command_timeout < 1) {
                <span class="error-text">Mindestens 1 Sekunde</span>
              }
            </label>
          </div>
          <div class="row mt-lg">
            <button (click)="save()" [disabled]="config.agent_offline_timeout < 10 || config.http_timeout < 1 || config.command_timeout < 1">Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'quality') {
        <div class="card">
          <h3>Quality Gates</h3>
          <p class="muted">Qualitaetsregeln fuer Task-Ausgaben und Autopilot-Durchsetzung.</p>
          <div class="grid cols-2">
            <label class="row gap-sm">
              <input type="checkbox" [(ngModel)]="qgEnabled" />
              Gates aktiviert
            </label>
            <label class="row gap-sm">
              <input type="checkbox" [(ngModel)]="qgAutopilotEnforce" />
              Im Autopilot erzwingen
            </label>
            <label>
              Min. Output Zeichen
              <input type="number" min="1" [(ngModel)]="qgMinOutputChars" [class.input-error]="qgMinOutputChars < 1" />
              @if (qgMinOutputChars < 1) {
                <span class="error-text">Mindestens 1 Zeichen</span>
              }
            </label>
            <label>
              Coding Keywords (Komma)
              <input [(ngModel)]="qgCodingKeywordsText" placeholder="code, implement, test" />
            </label>
            <label class="col-span-full">
              Erforderliche Marker bei Coding (Komma)
              <input [(ngModel)]="qgMarkersText" placeholder="pytest, passed, success" />
            </label>
          </div>
          <div class="row mt-md gap-sm">
            <button class="secondary" (click)="loadQualityGates()">Reload</button>
            <button (click)="saveQualityGates()" [disabled]="qgMinOutputChars < 1">Save Quality Gates</button>
          </div>
        </div>
        }
        @if (selectedSection === 'system') {
        <div class="card">
          <h3>Roh-Konfiguration (Hub)</h3>
          <p class="muted font-sm">Vorsicht: Direkte Bearbeitung der config.json des Hubs.</p>
          <textarea [(ngModel)]="configRaw" rows="10" class="font-mono w-full" [class.input-error]="configRawError"></textarea>
          @if (configRawError) {
            <span class="error-text">{{ configRawError }}</span>
          }
          <div class="row mt-sm">
            <button (click)="saveRaw()" class="button-outline" [disabled]="configRawError">Roh-Daten Speichern</button>
          </div>
        </div>
        }
        @if (selectedSection === 'llm' && llmHistory && llmHistory.length > 0) {
          <div class="card">
            <h3>LMStudio Modell-Historie</h3>
            <p class="muted">Zuletzt verwendete oder verfügbare Modelle von LMStudio.</p>
            <table class="standard-table">
              <thead>
                <tr>
                  <th>Modell ID</th>
                  <th>Zuletzt gesehen</th>
                </tr>
              </thead>
              <tbody>
                @for (h of llmHistory; track h) {
                  <tr>
                    <td class="font-mono font-sm">{{ h.model || h.id }}</td>
                    <td class="font-sm">{{ h.last_seen || '-' }}</td>
                  </tr>
                }
              </tbody>
            </table>
            <div class="row mt-lg">
              <button (click)="loadHistory()" class="button-outline">Aktualisieren</button>
            </div>
          </div>
        }
      </div>
    }
    `
})
export class SettingsComponent implements OnInit {
  private dir = inject(AgentDirectoryService);
  private api = inject(AgentApiService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);
  private auth = inject(UserAuthService);

  hub = this.dir.list().find(a => a.role === 'hub');
  allAgents = this.dir.list();
  config: any = {};
  configRaw = '';
  llmHistory: any[] = [];
  isAdmin = false;
  isDarkMode = document.body.classList.contains('dark-mode');
  qgEnabled = true;
  qgAutopilotEnforce = true;
  qgMinOutputChars = 8;
  qgCodingKeywordsText = 'code, implement, fix, refactor, bug, test, feature, endpoint';
  qgMarkersText = 'test, pytest, passed, success, lint, ok';
  selectedSection: 'account' | 'llm' | 'quality' | 'system' = 'llm';
  providerCatalog: any = null;
  benchmarkConfig: any = null;
  benchmarkRetentionDays = 90;
  benchmarkRetentionSamples = 2000;
  benchmarkProviderOrderTextValue = '';
  benchmarkModelOrderTextValue = '';
  benchmarkValidationError = '';
  configRawError = '';

  ngOnInit() {
    this.auth.user$.subscribe(user => {
      this.isAdmin = user?.role === 'admin';
    });
    this.load();
    this.loadHistory();
    this.loadProviderCatalog();
    this.loadBenchmarkConfig();
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

  setSection(section: 'account' | 'llm' | 'quality' | 'system') {
    this.selectedSection = section;
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
        this.syncQualityGatesFromConfig(cfg);
        this.loadProviderCatalog();
      },
      error: () => this.ns.error('Einstellungen konnten nicht geladen werden')
    });
  }

  loadProviderCatalog() {
    if (!this.hub) return;
    this.hubApi.listProviderCatalog(this.hub.url).subscribe({
      next: (catalog) => {
        this.providerCatalog = catalog || null;
        this.ensureProviderModelConsistency();
      },
      error: () => {
        this.providerCatalog = null;
      }
    });
  }

  loadBenchmarkConfig() {
    if (!this.hub) return;
    this.hubApi.getLlmBenchmarksConfig(this.hub.url).subscribe({
      next: (cfg) => {
        this.benchmarkConfig = cfg || null;
        this.syncBenchmarkConfigEditor(cfg || {});
        this.benchmarkValidationError = '';
      },
      error: () => {
        this.benchmarkConfig = null;
        this.benchmarkValidationError = '';
      }
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
    this.configRawError = '';
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
      this.configRawError = 'Ungültiges JSON: ' + (e instanceof Error ? e.message : String(e));
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

  getCatalogProviders(): Array<{ id: string; available: boolean; model_count: number }> {
    const providers = Array.isArray(this.providerCatalog?.providers) ? this.providerCatalog.providers : [];
    if (!providers.length) {
      return [
        { id: 'ollama', available: true, model_count: 0 },
        { id: 'lmstudio', available: true, model_count: 0 },
        { id: 'openai', available: true, model_count: 0 },
        { id: 'anthropic', available: true, model_count: 0 },
      ];
    }
    return providers
      .map((p: any) => ({
        id: String(p?.provider || ''),
        available: !!p?.available,
        model_count: Number(p?.model_count || 0),
      }))
      .filter((p) => !!p.id);
  }

  getCatalogModels(providerId: string): Array<{ id: string; display_name: string; context_length: number | null }> {
    const providers = Array.isArray(this.providerCatalog?.providers) ? this.providerCatalog.providers : [];
    const block = providers.find((p: any) => String(p?.provider || '') === String(providerId || ''));
    const models = Array.isArray(block?.models) ? block.models : [];
    if (!models.length) {
      return [];
    }
    return models
      .map((m: any) => ({
        id: String(m?.id || ''),
        display_name: String(m?.display_name || m?.id || ''),
        context_length: m?.context_length ?? null,
      }))
      .filter((m) => !!m.id);
  }

  ensureProviderModelConsistency() {
    const provider = this.getEffectiveProvider();
    const models = this.getCatalogModels(provider);
    if (!models.length) return;
    const current = String(this.config?.default_model || '').trim();
    if (!current || !models.some(m => m.id === current)) {
      this.config.default_model = models[0].id;
    }
  }

  isCurrentModelInCatalog(): boolean {
    const provider = this.getEffectiveProvider();
    const models = this.getCatalogModels(provider);
    const current = String(this.config?.default_model || '').trim();
    if (!current || !models.length) return false;
    return models.some((m) => m.id === current);
  }

  benchmarkProviderOrderText(): string {
    const arr = this.parseCommaList(this.benchmarkProviderOrderTextValue);
    return Array.isArray(arr) && arr.length ? arr.join(' -> ') : '-';
  }

  benchmarkModelOrderText(): string {
    const arr = this.parseCommaList(this.benchmarkModelOrderTextValue);
    return Array.isArray(arr) && arr.length ? arr.join(' -> ') : '-';
  }

  saveBenchmarkConfig() {
    if (!this.hub) return;
    this.benchmarkValidationError = '';

    const providerOrder = this.parseCommaList(this.benchmarkProviderOrderTextValue);
    const modelOrder = this.parseCommaList(this.benchmarkModelOrderTextValue);
    const providerAllowed = new Set(['proposal_backend', 'routing_effective_backend', 'llm_config_provider', 'default_provider', 'provider']);
    const modelAllowed = new Set(['proposal_model', 'llm_config_model', 'default_model', 'model']);

    const invalidProviderKeys = providerOrder.filter((k) => !providerAllowed.has(k));
    const invalidModelKeys = modelOrder.filter((k) => !modelAllowed.has(k));
    if (invalidProviderKeys.length || invalidModelKeys.length) {
      const invalidMsg = [
        invalidProviderKeys.length ? `ungueltige provider_order keys: ${invalidProviderKeys.join(', ')}` : '',
        invalidModelKeys.length ? `ungueltige model_order keys: ${invalidModelKeys.join(', ')}` : '',
      ]
        .filter(Boolean)
        .join(' | ');
      this.benchmarkValidationError = invalidMsg;
      this.ns.error('Benchmark-Konfiguration ist ungueltig');
      return;
    }

    const days = Math.max(1, Math.min(3650, Number(this.benchmarkRetentionDays || 90)));
    const samples = Math.max(50, Math.min(50000, Number(this.benchmarkRetentionSamples || 2000)));
    this.benchmarkRetentionDays = days;
    this.benchmarkRetentionSamples = samples;

    const payload = {
      benchmark_retention: {
        max_days: days,
        max_samples: samples,
      },
      benchmark_identity_precedence: {
        provider_order: providerOrder,
        model_order: modelOrder,
      },
    };
    this.hubApi.setConfig(this.hub.url, payload).subscribe({
      next: () => {
        this.ns.success('Benchmark-Konfiguration gespeichert');
        this.loadBenchmarkConfig();
      },
      error: () => this.ns.error('Benchmark-Konfiguration konnte nicht gespeichert werden'),
    });
  }

  private parseCommaList(text: string): string[] {
    return String(text || '')
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean);
  }

  private syncBenchmarkConfigEditor(cfg: any) {
    const retention = cfg?.retention || {};
    const precedence = cfg?.identity_precedence || {};
    this.benchmarkRetentionDays = Number(retention.max_days || 90);
    this.benchmarkRetentionSamples = Number(retention.max_samples || 2000);
    const providerOrder = Array.isArray(precedence.provider_order) ? precedence.provider_order : [];
    const modelOrder = Array.isArray(precedence.model_order) ? precedence.model_order : [];
    this.benchmarkProviderOrderTextValue = providerOrder.join(', ');
    this.benchmarkModelOrderTextValue = modelOrder.join(', ');
  }

  private syncQualityGatesFromConfig(cfg: any) {
    const qg = (cfg && cfg.quality_gates) ? cfg.quality_gates : {};
    this.qgEnabled = qg.enabled !== false;
    this.qgAutopilotEnforce = qg.autopilot_enforce !== false;
    this.qgMinOutputChars = Number(qg.min_output_chars || 8);
    this.qgCodingKeywordsText = Array.isArray(qg.coding_keywords) ? qg.coding_keywords.join(', ') : this.qgCodingKeywordsText;
    this.qgMarkersText = Array.isArray(qg.required_output_markers_for_coding)
      ? qg.required_output_markers_for_coding.join(', ')
      : this.qgMarkersText;
  }

  loadQualityGates() {
    if (!this.hub) return;
    this.hubApi.getConfig(this.hub.url).subscribe({
      next: cfg => this.syncQualityGatesFromConfig(cfg),
      error: () => this.ns.error('Quality-Gates konnten nicht geladen werden')
    });
  }

  saveQualityGates() {
    if (!this.hub) return;
    const toList = (text: string) =>
      (text || '')
        .split(',')
        .map(v => v.trim())
        .filter(Boolean);
    const payload = {
      quality_gates: {
        enabled: !!this.qgEnabled,
        autopilot_enforce: !!this.qgAutopilotEnforce,
        min_output_chars: Math.max(1, Number(this.qgMinOutputChars || 8)),
        coding_keywords: toList(this.qgCodingKeywordsText),
        required_output_markers_for_coding: toList(this.qgMarkersText),
      }
    };
    this.hubApi.setConfig(this.hub.url, payload).subscribe({
      next: () => {
        this.ns.success('Quality-Gates gespeichert');
        this.load();
      },
      error: () => this.ns.error('Quality-Gates konnten nicht gespeichert werden')
    });
  }
}
