var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { timeout } from 'rxjs';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
let WebhooksComponent = class WebhooksComponent {
    constructor() {
        this.dir = inject(AgentDirectoryService);
        this.hubApi = inject(HubApiService);
        this.ns = inject(NotificationService);
        this.hub = this.dir.list().find(a => a.role === 'hub');
        this.status = null;
        this.saving = false;
        this.testing = false;
        this.testResult = null;
        this.availableSources = [
            { id: 'generic', name: 'Generic', description: 'Allgemeine JSON-Webhooks' },
            { id: 'github', name: 'GitHub', description: 'Issues & Pull Requests' },
        ];
        this.config = {
            enabled_sources: ['generic', 'github'],
            webhook_secrets: {},
            auto_start_planner: true
        };
        this.testForm = {
            source: 'generic',
            payload: '',
            create_tasks: false
        };
        this.defaultPayload = JSON.stringify({
            title: 'Test Task von Webhook',
            description: 'Automatisch erstellt via Webhook-Test',
            priority: 'Medium'
        }, null, 2);
    }
    ngOnInit() {
        this.testForm.payload = this.defaultPayload;
        this.refresh();
    }
    refresh() {
        if (!this.hub)
            this.hub = this.dir.list().find(a => a.role === 'hub');
        if (!this.hub)
            return;
        this.hubApi.getTriggersStatus(this.hub.url).subscribe({
            next: (s) => {
                this.status = s;
                if (s) {
                    this.config.enabled_sources = s.enabled_sources || [];
                    this.config.webhook_secrets = s.webhook_secrets_configured?.reduce((acc, key) => {
                        acc[key] = '********';
                        return acc;
                    }, {}) || {};
                    this.config.auto_start_planner = s.auto_start_planner;
                }
            },
            error: (e) => this.ns.error(this.ns.fromApiError(e, 'Trigger Status konnte nicht geladen werden'))
        });
    }
    getWebhookUrl(source) {
        if (!this.hub)
            return '';
        return `${this.hub.url}/triggers/webhook/${source}`;
    }
    copyUrl(source) {
        const url = this.getWebhookUrl(source);
        navigator.clipboard.writeText(url).then(() => this.ns.success('URL kopiert'));
    }
    isSourceEnabled(source) {
        return this.config.enabled_sources?.includes(source) || false;
    }
    toggleSource(source, event) {
        const checked = event.target.checked;
        if (!this.config.enabled_sources)
            this.config.enabled_sources = [];
        if (checked && !this.config.enabled_sources.includes(source))
            this.config.enabled_sources.push(source);
        else if (!checked)
            this.config.enabled_sources = this.config.enabled_sources.filter((s) => s !== source);
    }
    getSecret(source) {
        return this.config.webhook_secrets?.[source] || '';
    }
    setSecret(source, event) {
        const value = event.target.value;
        if (!this.config.webhook_secrets)
            this.config.webhook_secrets = {};
        if (value)
            this.config.webhook_secrets[source] = value;
        else
            delete this.config.webhook_secrets[source];
    }
    saveConfig() {
        if (!this.hub)
            return;
        this.saving = true;
        const secretsToSend = { ...this.config.webhook_secrets };
        Object.keys(secretsToSend).forEach(key => {
            if (secretsToSend[key] === '********')
                delete secretsToSend[key];
        });
        this.hubApi.configureTriggers(this.hub.url, {
            enabled_sources: this.config.enabled_sources,
            webhook_secrets: secretsToSend,
            auto_start_planner: this.config.auto_start_planner
        }).subscribe({
            next: () => {
                this.saving = false;
                this.ns.success('Trigger-Konfiguration gespeichert');
                this.refresh();
            },
            error: (e) => {
                this.saving = false;
                this.ns.error(this.ns.fromApiError(e, 'Konfiguration konnte nicht gespeichert werden'));
            }
        });
    }
    testTrigger() {
        if (!this.hub)
            return;
        this.testing = true;
        this.testResult = null;
        let payload;
        try {
            payload = JSON.parse(this.testForm.payload);
        }
        catch {
            this.testResult = { error: 'Ungueltiges JSON' };
            this.ns.error('Ungueltiges JSON');
            this.testing = false;
            return;
        }
        this.hubApi.testTrigger(this.hub.url, { source: this.testForm.source, payload }).pipe(timeout(8000)).subscribe({
            next: (result) => {
                this.testing = false;
                this.testResult = result;
            },
            error: (e) => {
                this.testing = false;
                this.testResult = { error: this.ns.fromApiError(e, 'Test fehlgeschlagen') };
                this.ns.error(this.ns.fromApiError(e, 'Test fehlgeschlagen'));
            }
        });
    }
    sendWebhook() {
        if (!this.hub)
            return;
        this.testing = true;
        this.testResult = null;
        let payload;
        try {
            payload = JSON.parse(this.testForm.payload);
        }
        catch {
            this.testResult = { error: 'Ungueltiges JSON' };
            this.ns.error('Ungueltiges JSON');
            this.testing = false;
            return;
        }
        this.hubApi.testTrigger(this.hub.url, { source: this.testForm.source, payload }).pipe(timeout(8000)).subscribe({
            next: (result) => {
                this.testing = false;
                this.testResult = result;
                if (result?.would_create > 0)
                    this.ns.success(`${result.would_create} Task(s) wuerden erstellt werden`);
            },
            error: (e) => {
                this.testing = false;
                this.testResult = { error: this.ns.fromApiError(e, 'Webhook-Test fehlgeschlagen') };
                this.ns.error(this.ns.fromApiError(e, 'Webhook-Test fehlgeschlagen'));
            }
        });
    }
};
WebhooksComponent = __decorate([
    Component({
        standalone: true,
        selector: 'app-webhooks',
        imports: [CommonModule, FormsModule],
        styles: [`
    .wh-subtitle { margin-top: 4px; }
    .wh-grid-top { margin-top: 12px; }
    .wh-muted-card { background: #f8fafc; }
    .wh-section { margin-top: 14px; background: #fafafa; }
    .wh-section-title { margin-top: 0; }
    .wh-help { font-size: 12px; }
    .wh-source-list { margin-top: 10px; }
    .wh-source-card { padding: 12px; background: #fff; border: 1px solid #e5e7eb; border-radius: 6px; margin-bottom: 8px; }
    .wh-source-row { justify-content: space-between; align-items: center; }
    .wh-source-toggle { display: flex; align-items: center; gap: 6px; margin: 0; }
    .wh-source-desc { margin-left: 8px; font-size: 12px; }
    .wh-url-row { margin-top: 8px; display: flex; gap: 8px; }
    .wh-url-code { flex: 1; padding: 8px; background: #f3f4f6; border-radius: 4px; font-size: 12px; overflow: hidden; text-overflow: ellipsis; }
    .wh-small-btn { padding: 4px 12px; }
    .wh-grid { margin-top: 10px; }
    .wh-save-btn { margin-top: 10px; }
    .wh-test-actions { gap: 8px; margin-top: 12px; }
    .wh-result { margin-top: 12px; padding: 12px; background: #f0fdf4; border-radius: 6px; }
    .wh-result-pre { margin: 8px 0 0; font-size: 11px; overflow: auto; }
    .wh-guide { margin: 10px 0; padding-left: 20px; font-size: 13px; }
  `],
        template: `
    <div class="card">
      <h3>Webhooks und Trigger</h3>
      <p class="muted wh-subtitle">Externe Integrationen fuer automatische Task-Erstellung.</p>

      @if (status) {
        <div class="grid cols-4 wh-grid-top">
          <div class="card wh-muted-card">
            <div class="muted">Webhooks empfangen</div>
            <strong>{{ status.stats?.webhooks_received || 0 }}</strong>
          </div>
          <div class="card wh-muted-card">
            <div class="muted">Tasks erstellt</div>
            <strong>{{ status.stats?.tasks_created || 0 }}</strong>
          </div>
          <div class="card wh-muted-card">
            <div class="muted">Abgelehnt</div>
            <strong class="danger">{{ status.stats?.rejected || 0 }}</strong>
          </div>
          <div class="card wh-muted-card">
            <div class="muted">Aktive Quellen</div>
            <strong>{{ status.enabled_sources?.length || 0 }}</strong>
          </div>
        </div>
      }

      <div class="card wh-section">
        <h4 class="wh-section-title" data-testid="webhooks-urls-title">Webhook-URLs</h4>
        <p class="muted wh-help">Nutze diese URLs fuer externe Integrationen:</p>

        <div class="wh-source-list">
          @for (source of availableSources; track source.id) {
            <div class="wh-source-card">
              <div class="row wh-source-row">
                <div>
                  <strong>{{ source.name }}</strong>
                  <span class="muted wh-source-desc">{{ source.description }}</span>
                </div>
                <label class="wh-source-toggle" [attr.title]="'Aktiviert/Deaktiviert Quelle ' + source.name">
                  <input type="checkbox" [attr.data-testid]="'webhooks-source-enabled-' + source.id" [checked]="isSourceEnabled(source.id)" (change)="toggleSource(source.id, $event)" />
                  Aktiv
                </label>
              </div>
              <div class="wh-url-row">
                <code class="wh-url-code" [attr.data-testid]="'webhooks-url-' + source.id">{{ getWebhookUrl(source.id) }}</code>
                <button class="secondary wh-small-btn" [attr.data-testid]="'webhooks-copy-' + source.id" (click)="copyUrl(source.id)">Kopieren</button>
              </div>
            </div>
          }
        </div>
      </div>

      <div class="card wh-section">
        <h4 class="wh-section-title">Webhook-Secrets</h4>
        <p class="muted wh-help">Optional: Signatur-Validierung fuer erhoehte Sicherheit.</p>

        <div class="grid cols-2 wh-grid">
          @for (source of availableSources; track source.id) {
            <label>
              {{ source.name }} Secret
              <input type="password" [value]="getSecret(source.id)" (input)="setSecret(source.id, $event)" placeholder="Optional - leer lassen fuer keine Validierung" />
            </label>
          }
        </div>
        <button class="wh-save-btn" data-testid="webhooks-save-config" (click)="saveConfig()" [disabled]="saving" title="Speichert Trigger- und Secret-Konfiguration">Speichern</button>
      </div>

      <div class="card wh-section">
        <h4 class="wh-section-title" data-testid="webhooks-test-title">Webhook testen</h4>
        <div class="grid cols-2 wh-grid">
          <label>
            Quelle
            <select data-testid="webhooks-test-source" [(ngModel)]="testForm.source">
              @for (s of availableSources; track s.id) {
                <option [value]="s.id">{{ s.name }}</option>
              }
            </select>
          </label>
          <label>
              <input type="checkbox" [(ngModel)]="testForm.create_tasks" />
              Tasks erstellen (sonst nur Test)
            </label>
        </div>
        <div class="wh-grid">
          <label>
            Payload (JSON)
            <textarea data-testid="webhooks-test-payload" [(ngModel)]="testForm.payload" rows="5" class="ap-goal-input"></textarea>
          </label>
        </div>
        <div class="row wh-test-actions">
          <button data-testid="webhooks-test-run" (click)="testTrigger()" [disabled]="testing">Testen</button>
          <button data-testid="webhooks-send" class="secondary" (click)="sendWebhook()" [disabled]="testing">Webhook senden</button>
        </div>

        @if (testResult) {
          <div class="wh-result" data-testid="webhooks-test-result">
            <strong>Ergebnis:</strong>
            <pre class="wh-result-pre">{{ testResult | json }}</pre>
          </div>
        }
      </div>

      <div class="card wh-section">
        <h4 class="wh-section-title">GitHub Integration</h4>
        <p class="muted wh-help">So verbindest du GitHub mit Ananta:</p>
        <ol class="wh-guide">
          <li>Oeffne dein GitHub Repository -> Settings -> Webhooks</li>
          <li>Klicke "Add webhook"</li>
          <li>Payload URL: <code>{{ getWebhookUrl('github') }}</code></li>
          <li>Content type: <code>application/json</code></li>
          <li>Secret: (optional) dein konfiguriertes GitHub Secret</li>
          <li>Events: Issues, Pull requests</li>
        </ol>
      </div>
    </div>
  `
    })
], WebhooksComponent);
export { WebhooksComponent };
//# sourceMappingURL=webhooks.component.js.map