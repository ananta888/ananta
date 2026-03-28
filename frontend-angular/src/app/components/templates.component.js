var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
let TemplatesComponent = class TemplatesComponent {
    constructor() {
        this.dir = inject(AgentDirectoryService);
        this.hubApi = inject(HubApiService);
        this.ns = inject(NotificationService);
        this.userAuth = inject(UserAuthService);
        this.items = [];
        this.roles = [];
        this.teams = [];
        this.teamTypes = [];
        this.err = '';
        this.form = { name: '', description: '', prompt_template: '' };
        this.promptTemplateHint = 'Verwenden Sie {{variable}} fuer Platzhalter.';
        this.allowedVars = ["agent_name", "task_title", "task_description", "team_name", "role_name", "team_goal", "anforderungen", "funktion", "feature_name", "title", "description", "task", "endpoint_name", "beschreibung", "sprache", "api_details"];
        this.hub = this.dir.list().find(a => a.role === 'hub');
        this.isAdmin = false;
        this.userAuth.user$.subscribe(user => {
            this.isAdmin = user?.role === 'admin';
        });
        this.refresh();
    }
    normalizeListResponse(value) {
        let current = value;
        for (let i = 0; i < 4; i += 1) {
            if (Array.isArray(current))
                return current;
            if (!current || typeof current !== 'object')
                break;
            if ('status' in current && 'data' in current) {
                current = current.data;
                continue;
            }
            if ('data' in current) {
                current = current.data;
                continue;
            }
            break;
        }
        return Array.isArray(current) ? current : [];
    }
    refresh() {
        if (!this.hub)
            return;
        this.hubApi.getConfig(this.hub.url).subscribe({
            next: cfg => {
                if (Array.isArray(cfg.template_variables_allowlist) && cfg.template_variables_allowlist.length) {
                    this.allowedVars = cfg.template_variables_allowlist;
                }
            }
        });
        this.hubApi.listTemplates(this.hub.url).subscribe({
            next: r => this.items = this.normalizeListResponse(r),
            error: () => this.ns.error('Templates konnten nicht geladen werden')
        });
        this.hubApi.listTeamRoles(this.hub.url).subscribe({ next: r => this.roles = this.normalizeListResponse(r), error: () => { } });
        this.hubApi.listTeams(this.hub.url).subscribe({ next: r => this.teams = this.normalizeListResponse(r), error: () => { } });
        this.hubApi.listTeamTypes(this.hub.url).subscribe({ next: r => this.teamTypes = this.normalizeListResponse(r), error: () => { } });
    }
    getRoleUsageCount(templateId) {
        return this.roles.filter(r => r.default_template_id === templateId).length;
    }
    getTypeUsageCount(templateId) {
        let count = 0;
        for (const t of this.teamTypes) {
            const mappings = t.role_templates || {};
            for (const roleId of Object.keys(mappings)) {
                if (mappings[roleId] === templateId)
                    count += 1;
            }
        }
        return count;
    }
    getMemberUsageCount(templateId) {
        let count = 0;
        for (const team of this.teams) {
            for (const member of team.members || []) {
                if (member.custom_template_id === templateId)
                    count += 1;
            }
        }
        return count;
    }
    getUnknownVars() {
        if (!this.form.prompt_template)
            return [];
        const matches = this.form.prompt_template.match(/\{\{([a-zA-Z0-9_]+)\}\}/g) || [];
        const vars = matches.map((m) => m.replace(/\{\{|\}\}/g, ''));
        return vars.filter((v) => !this.allowedVars.includes(v));
    }
    create() {
        if (!this.isAdmin) {
            this.ns.error('Admin-Rechte erforderlich');
            return;
        }
        if (!this.hub) {
            this.err = 'Kein Hub konfiguriert';
            return;
        }
        if (!this.form.name || !this.form.prompt_template) {
            this.ns.error('Name und Template sind erforderlich');
            return;
        }
        const obs = this.form.id
            ? this.hubApi.updateTemplate(this.hub.url, this.form.id, this.form)
            : this.hubApi.createTemplate(this.hub.url, this.form);
        obs.subscribe({
            next: r => {
                this.form = { name: '', description: '', prompt_template: '' };
                this.err = '';
                this.ns.success('Template gespeichert');
                if (r?.warnings?.length) {
                    const details = r.warnings.map((w) => w.details || "").filter(Boolean).join("; ");
                    if (details)
                        this.ns.info(`Template saved with warnings: ${details}`);
                }
                this.refresh();
            },
            error: (e) => {
                if (e.error && e.error.details) {
                    this.err = e.error.details;
                    this.ns.error(e.error.details);
                }
                else {
                    this.ns.error('Fehler beim Speichern');
                }
            }
        });
    }
    edit(t) {
        if (!this.isAdmin) {
            this.ns.error('Admin-Rechte erforderlich');
            return;
        }
        this.form = { ...t };
        this.ns.info('Template in Editor geladen');
    }
    del(id) {
        if (!this.isAdmin) {
            this.ns.error('Admin-Rechte erforderlich');
            return;
        }
        if (!this.hub || !confirm('Vorlage wirklich loeschen?'))
            return;
        this.hubApi.deleteTemplate(this.hub.url, id).subscribe({
            next: (res) => {
                this.items = this.items.filter(t => t.id !== id);
                this.ns.success('Geloescht');
                const cleared = res?.cleared;
                if (cleared && (cleared.roles?.length || cleared.team_type_links?.length || cleared.team_members?.length || cleared.teams?.length)) {
                    const parts = [];
                    if (cleared.roles?.length)
                        parts.push(`Rollen: ${cleared.roles.length}`);
                    if (cleared.team_type_links?.length)
                        parts.push(`Team-Typen: ${cleared.team_type_links.length}`);
                    if (cleared.team_members?.length)
                        parts.push(`Team-Mitglieder: ${cleared.team_members.length}`);
                    if (cleared.teams?.length)
                        parts.push(`Teams: ${cleared.teams.length}`);
                    if (parts.length)
                        this.ns.info(`Referenzen entfernt (${parts.join(', ')})`);
                }
                this.refresh();
            },
            error: (e) => {
                const code = e?.error?.error;
                if (code === 'template_in_use') {
                    this.ns.error('Template wird noch verwendet. Bitte Zuordnungen entfernen.');
                    return;
                }
                const msg = e?.error?.message || code || 'Loeschen fehlgeschlagen';
                this.ns.error(msg);
            }
        });
    }
};
TemplatesComponent = __decorate([
    Component({
        standalone: true,
        selector: 'app-templates',
        imports: [FormsModule],
        template: `
    <div class="row flex-between">
      <h2>Templates (Hub)</h2>
      <button (click)="refresh()" class="button-outline">Aktualisieren</button>
    </div>
    <p class="muted">Verwalten und erstellen Sie Prompt-Templates.</p>
    @if (!isAdmin) {
      <div class="muted mb-md">Template-Verwaltung ist nur fuer Admins verfuegbar.</div>
    }

    <div class="card grid">
      <label>Name <input [(ngModel)]="form.name" placeholder="Name" [disabled]="!isAdmin"></label>
      <label>Beschreibung <input [(ngModel)]="form.description" placeholder="Beschreibung" [disabled]="!isAdmin"></label>
      <label>Prompt Template
        <textarea [(ngModel)]="form.prompt_template" rows="6" placeholder="{{ promptTemplateHint }}" [disabled]="!isAdmin"></textarea>
      </label>
      <div class="muted var-hint">
        Erlaubte Variablen: @for (v of allowedVars; track v) {
        <span class="var-tag" [title]="'Variable: {{'+v+'}}'">{{ '{' + '{' + v + '}' + '}' }}</span>
      }
      </div>
      @if (getUnknownVars().length > 0) {
        <div class="danger unknown-vars">Unbekannte Variablen: {{ getUnknownVars().join(', ') }}</div>
      }
      <div class="row">
        <button (click)="create()" [disabled]="!isAdmin">Anlegen / Speichern</button>
        <button (click)="form = { name: '', description: '', prompt_template: '' }" class="button-outline" [disabled]="!isAdmin">Neu</button>
        @if (err) {
          <span class="danger">{{err}}</span>
        }
      </div>
    </div>

    @if (items.length) {
      <div class="grid cols-2 mt-20">
        @for (t of items; track t) {
          <div class="card">
            <div class="row space-between">
              <strong>{{t.name}}</strong>
              <div class="row">
                <button (click)="edit(t)" class="button-outline btn-sm-action" [disabled]="!isAdmin">Edit</button>
                <button (click)="del(t.id)" class="danger btn-sm-action" [disabled]="!isAdmin">Loeschen</button>
              </div>
            </div>
            <div class="muted">{{t.description}}</div>
            <div class="muted usage-info">Nutzung: Rollen {{getRoleUsageCount(t.id)}}, Typ-Zuordnung {{getTypeUsageCount(t.id)}}, Team-Mitglieder {{getMemberUsageCount(t.id)}}</div>
            <details class="details-mt-8">
              <summary>Prompt ansehen</summary>
              <pre class="prompt-preview">{{t.prompt_template}}</pre>
            </details>
          </div>
        }
      </div>
    }
    `
    })
], TemplatesComponent);
export { TemplatesComponent };
//# sourceMappingURL=templates.component.js.map