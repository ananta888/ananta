import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { UiSkeletonComponent } from './ui-skeleton.component';
import { AdminFacade } from '../features/admin/admin.facade';

@Component({
  standalone: true,
  selector: 'app-templates',
  imports: [FormsModule, UiSkeletonComponent],
  template: `
    <div class="row flex-between">
      <h2>Templates (Hub)</h2>
      <button (click)="refresh()" class="button-outline">Aktualisieren</button>
    </div>
    <p class="muted">
      Verwalten und erstellen Sie Prompt-Templates.
      <a href="/docs/template-authoring-guide.md" target="_blank" rel="noopener">Autoren-Guide</a>
    </p>
    @if (!isAdmin) {
      <div class="muted mb-md">Template-Verwaltung ist nur fuer Admins verfuegbar.</div>
    }

    @if (loading) {
      <div class="card">
        <app-ui-skeleton [count]="2" [lineCount]="5"></app-ui-skeleton>
      </div>
    }

    <div class="card grid">
      <label>Name <input [(ngModel)]="form.name" placeholder="Name" [disabled]="!isAdmin"></label>
      <label>Beschreibung <input [(ngModel)]="form.description" placeholder="Beschreibung" [disabled]="!isAdmin"></label>
      <label>Validierungs-Kontext
        <select [(ngModel)]="validationContext" [disabled]="!isAdmin" (ngModelChange)="onTemplateChanged()">
          @for (scope of supportedScopes; track scope) {
            <option [ngValue]="scope">{{ scope }}</option>
          }
        </select>
      </label>
      <label>Beispiel-Kontext fuer Vorschau
        <select [(ngModel)]="sampleContext" [disabled]="!isAdmin || !sampleContextKeys.length">
          @for (scope of sampleContextKeys; track scope) {
            <option [ngValue]="scope">{{ scope }}</option>
          }
        </select>
      </label>
      <label>Prompt Template
        <textarea
          [(ngModel)]="form.prompt_template"
          rows="8"
          placeholder="{{ promptTemplateHint }}"
          [disabled]="!isAdmin"
          (ngModelChange)="onTemplateChanged()"
        ></textarea>
      </label>

      @if (templateValidationStrict) {
        <div class="muted">Strict-Mode aktiv: Validierungsfehler blockieren das Speichern.</div>
      }

      <div class="muted var-hint">
        Verfuegbare Variablen je Scope:
        @if (variableScopeGroups.length) {
          @for (group of variableScopeGroups; track group.scope) {
            <div class="scope-group">
              <strong>{{ group.scope }}</strong>:
              @for (entry of group.variables; track entry.name) {
                <span
                  class="var-tag"
                  [title]="entryTooltip(entry)"
                >{{ '{' + '{' + entry.name + '}' + '}' }}</span>
              }
            </div>
          }
        } @else {
          @for (v of allowedVars; track v) {
            <span class="var-tag">{{ '{' + '{' + v + '}' + '}' }}</span>
          }
        }
      </div>

      @if (getUnknownVars().length > 0) {
        <div class="danger unknown-vars">Unbekannte Variablen: {{ getUnknownVars().join(', ') }}</div>
      }
      @if (getContextInvalidVars().length > 0) {
        <div class="danger unknown-vars">
          Im Kontext '{{ validationContext }}' nicht verfuegbar: {{ getContextInvalidVars().join(', ') }}
        </div>
      }
      @if (getDeprecatedVars().length > 0) {
        <div class="muted">
          Legacy-Variablen (Migration empfohlen): {{ getDeprecatedVars().join(', ') }}
        </div>
      }
      @if (validationResult?.summary) {
        <div class="muted">
          Validierung: {{ validationResult.summary.found_count }} Variablen,
          {{ validationResult.summary.unknown_count }} unbekannt,
          {{ validationResult.summary.context_invalid_count }} kontext-ungueltig.
        </div>
      }

      <div class="row">
        <button (click)="validateCurrentTemplate(true)" [disabled]="!isAdmin">Validieren</button>
        <button (click)="previewTemplate()" class="button-outline" [disabled]="!isAdmin">Vorschau</button>
        <button (click)="create()" [disabled]="!isAdmin">Anlegen / Speichern</button>
        <button (click)="resetForm()" class="button-outline" [disabled]="!isAdmin">Neu</button>
        @if (err) {
          <span class="danger">{{ err }}</span>
        }
      </div>

      @if (previewResult?.preview) {
        <details open>
          <summary>Template Vorschau</summary>
          <div class="muted">
            Kontext: {{ previewResult.context_scope || validationContext }},
            Beispiel: {{ previewResult.sample_context || sampleContext }}
          </div>
          <pre class="prompt-preview">{{ previewResult.preview.rendered_text }}</pre>
          @if ((previewResult.preview.missing_variables || []).length) {
            <div class="danger">Fehlende Werte: {{ previewResult.preview.missing_variables.join(', ') }}</div>
          }
        </details>
      }
    </div>

    @if (!loading && items.length) {
      <div class="grid cols-2 mt-20">
        @if (loadingMeta) {
          <div class="muted">Nutzungsinformationen werden geladen ...</div>
        }
        @for (t of items; track t) {
          <div class="card">
            <div class="row space-between">
              <strong>{{ t.name }}</strong>
              <div class="row">
                <button (click)="edit(t)" class="button-outline btn-sm-action" [disabled]="!isAdmin">Edit</button>
                <button (click)="del(t.id)" class="danger btn-sm-action" [disabled]="!isAdmin">Loeschen</button>
              </div>
            </div>
            <div class="muted">{{ t.description }}</div>
            <div class="muted usage-info">
              Nutzung: Rollen {{ getRoleUsageCount(t.id) }}, Typ-Zuordnung {{ getTypeUsageCount(t.id) }}, Team-Mitglieder {{ getMemberUsageCount(t.id) }}
            </div>
            <details class="details-mt-8">
              <summary>Prompt ansehen</summary>
              <pre class="prompt-preview">{{ t.prompt_template }}</pre>
            </details>
          </div>
        }
      </div>
    } @else if (!loading) {
      <div class="card mt-20 muted">Noch keine Templates vorhanden.</div>
    }
  `,
})
export class TemplatesComponent {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(AdminFacade);
  private ns = inject(NotificationService);
  private userAuth = inject(UserAuthService);

  items: any[] = [];
  roles: any[] = [];
  teams: any[] = [];
  teamTypes: any[] = [];

  err = '';
  form: any = { name: '', description: '', prompt_template: '' };
  promptTemplateHint = 'Verwenden Sie {{variable}} fuer Platzhalter.';

  allowedVars: string[] = [];
  supportedScopes: string[] = ['task'];
  validationContext = 'task';
  sampleContext = 'task';
  sampleContexts: Record<string, any> = {};
  sampleContextKeys: string[] = [];
  variableRegistry: any = null;
  variableScopeGroups: Array<{ scope: string; variables: any[] }> = [];
  templateValidationStrict = false;
  validationResult: any = null;
  previewResult: any = null;

  hub = this.dir.list().find((a) => a.role === 'hub');
  isAdmin = false;
  loading = false;
  loadingMeta = false;
  private refreshSafetyTimer?: ReturnType<typeof setTimeout>;
  private refreshMetaSafetyTimer?: ReturnType<typeof setTimeout>;
  private adminRoleResolved = false;

  constructor() {
    this.userAuth.user$.subscribe((user) => {
      if (user?.role) {
        this.isAdmin = user.role === 'admin';
        this.adminRoleResolved = true;
        return;
      }
      if (!this.adminRoleResolved && this.userAuth.token) {
        this.adminRoleResolved = true;
        this.userAuth.getMe().subscribe({
          next: (me) => {
            this.isAdmin = me?.role === 'admin';
          },
          error: () => {
            this.isAdmin = false;
          },
        });
      }
    });
    this.refresh();
  }

  private normalizeListResponse(value: any): any[] {
    let current = value;
    for (let i = 0; i < 4; i += 1) {
      if (Array.isArray(current)) return current;
      if (!current || typeof current !== 'object') break;
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

  private normalizeObjectResponse(value: any): any {
    let current = value;
    for (let i = 0; i < 4; i += 1) {
      if (!current || typeof current !== 'object') break;
      if ('status' in current && 'data' in current) {
        current = current.data;
        continue;
      }
      if ('data' in current && typeof current.data === 'object') {
        current = current.data;
        continue;
      }
      break;
    }
    return current;
  }

  refresh() {
    if (!this.hub) return;
    if (this.refreshSafetyTimer) {
      clearTimeout(this.refreshSafetyTimer);
      this.refreshSafetyTimer = undefined;
    }
    if (this.refreshMetaSafetyTimer) {
      clearTimeout(this.refreshMetaSafetyTimer);
      this.refreshMetaSafetyTimer = undefined;
    }
    this.loading = true;
    this.loadingMeta = true;

    this.refreshSafetyTimer = setTimeout(() => {
      this.loading = false;
      this.refreshSafetyTimer = undefined;
    }, 15000);
    this.refreshMetaSafetyTimer = setTimeout(() => {
      this.loadingMeta = false;
      this.refreshMetaSafetyTimer = undefined;
    }, 20000);

    let primaryPending = 4;
    let metaPending = 3;
    const donePrimary = () => {
      primaryPending -= 1;
      if (primaryPending <= 0) {
        this.loading = false;
        if (this.refreshSafetyTimer) {
          clearTimeout(this.refreshSafetyTimer);
          this.refreshSafetyTimer = undefined;
        }
      }
    };
    const doneMeta = () => {
      metaPending -= 1;
      if (metaPending <= 0) {
        this.loadingMeta = false;
        if (this.refreshMetaSafetyTimer) {
          clearTimeout(this.refreshMetaSafetyTimer);
          this.refreshMetaSafetyTimer = undefined;
        }
      }
    };

    this.hubApi.getConfig(this.hub.url).pipe(finalize(donePrimary)).subscribe({
      next: (cfg) => {
        const raw = this.normalizeObjectResponse(cfg) || {};
        this.templateValidationStrict = !!raw?.template_variable_validation?.strict;
        if (Array.isArray(raw.template_variables_allowlist) && raw.template_variables_allowlist.length) {
          this.allowedVars = raw.template_variables_allowlist;
        }
      },
      error: () => {},
    });

    this.hubApi.getTemplateVariableRegistry(this.hub.url).pipe(finalize(donePrimary)).subscribe({
      next: (payload) => {
        const registry = this.normalizeObjectResponse(payload) || {};
        this.variableRegistry = registry;
        this.allowedVars = Array.isArray(registry.allowed_names) ? registry.allowed_names : this.allowedVars;
        this.supportedScopes = Array.isArray(registry.supported_context_scopes) && registry.supported_context_scopes.length
          ? registry.supported_context_scopes
          : ['task'];
        if (!this.supportedScopes.includes(this.validationContext)) {
          this.validationContext = this.supportedScopes[0];
        }
        this.rebuildScopeGroups();
      },
      error: () => {},
    });

    this.hubApi.getTemplateSampleContexts(this.hub.url).pipe(finalize(donePrimary)).subscribe({
      next: (payload) => {
        const data = this.normalizeObjectResponse(payload) || {};
        this.sampleContexts = (data.contexts && typeof data.contexts === 'object') ? data.contexts : {};
        this.sampleContextKeys = Object.keys(this.sampleContexts);
        if (typeof data.default_context_scope === 'string' && this.supportedScopes.includes(data.default_context_scope)) {
          this.validationContext = data.default_context_scope;
        }
        if (!this.sampleContextKeys.includes(this.sampleContext)) {
          this.sampleContext = this.sampleContextKeys[0] || this.validationContext;
        }
      },
      error: () => {},
    });

    this.hubApi.listTemplates(this.hub.url).pipe(finalize(donePrimary)).subscribe({
      next: (r) => (this.items = this.normalizeListResponse(r)),
      error: () => this.ns.error('Templates konnten nicht geladen werden'),
    });

    this.hubApi
      .listTeamRoles(this.hub.url)
      .pipe(finalize(doneMeta))
      .subscribe({ next: (r) => (this.roles = this.normalizeListResponse(r)), error: () => {} });
    this.hubApi
      .listTeams(this.hub.url)
      .pipe(finalize(doneMeta))
      .subscribe({ next: (r) => (this.teams = this.normalizeListResponse(r)), error: () => {} });
    this.hubApi
      .listTeamTypes(this.hub.url)
      .pipe(finalize(doneMeta))
      .subscribe({ next: (r) => (this.teamTypes = this.normalizeListResponse(r)), error: () => {} });
  }

  private rebuildScopeGroups() {
    const groups: Array<{ scope: string; variables: any[] }> = [];
    const byScope = this.variableRegistry?.by_scope || {};
    const byName: Record<string, any> = {};
    for (const variable of this.variableRegistry?.variables || []) {
      const key = String(variable?.name || '').trim();
      if (key) byName[key] = variable;
    }
    for (const scope of Object.keys(byScope)) {
      const names = Array.isArray(byScope[scope]) ? byScope[scope] : [];
      const variables = names
        .map((name: string) => byName[name] || { name })
        .sort((a: any, b: any) => String(a.name || '').localeCompare(String(b.name || '')));
      groups.push({ scope, variables });
    }
    groups.sort((a, b) => a.scope.localeCompare(b.scope));
    this.variableScopeGroups = groups;
  }

  entryTooltip(entry: any): string {
    const description = String(entry?.description || '').trim();
    const source = String(entry?.value_source || '').trim();
    const stability = String(entry?.stability || '').trim();
    const alias = String(entry?.alias_of || '').trim();
    const parts = [
      `{{${entry?.name || ''}}}`,
      description || 'Keine Beschreibung',
      source ? `Quelle: ${source}` : '',
      stability ? `Stabilitaet: ${stability}` : '',
      alias ? `Alias von: ${alias}` : '',
    ].filter(Boolean);
    return parts.join(' | ');
  }

  onTemplateChanged() {
    this.err = '';
    this.previewResult = null;
    this.validationResult = null;
  }

  resetForm() {
    this.form = { name: '', description: '', prompt_template: '' };
    this.err = '';
    this.previewResult = null;
    this.validationResult = null;
  }

  getRoleUsageCount(templateId: string): number {
    return this.roles.filter((r) => r.default_template_id === templateId).length;
  }

  getTypeUsageCount(templateId: string): number {
    let count = 0;
    for (const t of this.teamTypes) {
      const mappings = t.role_templates || {};
      for (const roleId of Object.keys(mappings)) {
        if (mappings[roleId] === templateId) count += 1;
      }
    }
    return count;
  }

  getMemberUsageCount(templateId: string): number {
    let count = 0;
    for (const team of this.teams) {
      for (const member of team.members || []) {
        if (member.custom_template_id === templateId) count += 1;
      }
    }
    return count;
  }

  getUnknownVars(): string[] {
    if (Array.isArray(this.validationResult?.unknown_variables)) {
      return this.validationResult.unknown_variables;
    }
    if (!this.form.prompt_template) return [];
    const matches = this.form.prompt_template.match(/\{\{([a-zA-Z0-9_]+)\}\}/g) || [];
    const vars = matches.map((m: string) => m.replace(/\{\{|\}\}/g, ''));
    return vars.filter((v: string, idx: number) => vars.indexOf(v) === idx && !this.allowedVars.includes(v));
  }

  getContextInvalidVars(): string[] {
    return Array.isArray(this.validationResult?.context_invalid_variables)
      ? this.validationResult.context_invalid_variables
      : [];
  }

  getDeprecatedVars(): string[] {
    return Array.isArray(this.validationResult?.deprecated_variables)
      ? this.validationResult.deprecated_variables
      : [];
  }

  validateCurrentTemplate(showNotification = false) {
    if (!this.hub || !this.form.prompt_template) return;
    this.hubApi
      .validateTemplate(this.hub.url, {
        prompt_template: this.form.prompt_template,
        context_scope: this.validationContext,
      })
      .subscribe({
        next: (response) => {
          this.validationResult = this.normalizeObjectResponse(response);
          if (showNotification) {
            if (this.validationResult?.is_valid) {
              this.ns.success('Template validiert');
            } else {
              this.ns.info('Template validiert mit Hinweisen');
            }
          }
        },
        error: () => {
          if (showNotification) this.ns.error('Template-Validierung fehlgeschlagen');
        },
      });
  }

  previewTemplate() {
    if (!this.hub || !this.form.prompt_template) return;
    this.hubApi
      .previewTemplate(this.hub.url, {
        prompt_template: this.form.prompt_template,
        context_scope: this.validationContext,
        sample_context: this.sampleContext,
      })
      .subscribe({
        next: (response) => {
          this.previewResult = this.normalizeObjectResponse(response);
          this.validationResult = this.previewResult?.validation || this.validationResult;
          this.ns.success('Vorschau aktualisiert');
        },
        error: () => this.ns.error('Template-Vorschau fehlgeschlagen'),
      });
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

    this.form = { ...this.form, name: this.form.name.trim() };
    const payload = { ...this.form, validation_context: this.validationContext };
    const obs = this.form.id
      ? this.hubApi.updateTemplate(this.hub.url, this.form.id, payload)
      : this.hubApi.createTemplate(this.hub.url, payload);

    obs.subscribe({
      next: (r) => {
        this.resetForm();
        this.ns.success('Template gespeichert');
        if (r?.warnings?.length) {
          const details = r.warnings
            .map((w: any) => w.details || '')
            .filter(Boolean)
            .join('; ');
          if (details) this.ns.info(`Template mit Hinweisen gespeichert: ${details}`);
        }
        this.refresh();
      },
      error: (e) => {
        const message = e?.error?.message || e?.error?.error;
        if (message === 'unknown_template_variables') {
          const unknown = e?.error?.data?.unknown_variables || [];
          const detail = unknown.length
            ? `Unbekannte Variablen: ${unknown.join(', ')}`
            : 'Unbekannte Variablen im Template.';
          this.err = detail;
          this.ns.error(detail);
          return;
        }
        if (message === 'context_unavailable_template_variables') {
          const invalid = e?.error?.data?.context_invalid_variables || [];
          const scope = e?.error?.data?.context_scope || this.validationContext;
          const detail = invalid.length
            ? `Im Kontext '${scope}' nicht verfuegbar: ${invalid.join(', ')}`
            : 'Template verwendet im gewaehlten Kontext nicht verfuegbare Variablen.';
          this.err = detail;
          this.ns.error(detail);
          return;
        }
        if (message === 'template_validation_failed') {
          const unknown = e?.error?.data?.unknown_variables || [];
          const invalid = e?.error?.data?.context_invalid_variables || [];
          const detail = `Validierung fehlgeschlagen. Unbekannt: ${unknown.join(', ') || '-'}; Kontext-ungueltig: ${invalid.join(', ') || '-'}`;
          this.err = detail;
          this.ns.error(detail);
          return;
        }
        if (message === 'template_name_exists') {
          const name = e?.error?.data?.name || this.form.name;
          const detail = `Ein Template mit diesem Namen existiert bereits: ${name}`;
          this.err = detail;
          this.ns.error(detail);
          return;
        }
        if (message === 'template_name_required') {
          this.err = 'Template-Name ist erforderlich.';
          this.ns.error(this.err);
          return;
        }
        if (e?.error?.details) {
          this.err = e.error.details;
          this.ns.error(e.error.details);
          return;
        }
        this.err = 'Fehler beim Speichern';
        this.ns.error(this.err);
      },
    });
  }

  edit(t: any) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    this.form = { ...t };
    this.validationResult = null;
    this.previewResult = null;
    this.ns.info('Template in Editor geladen');
  }

  del(id: string) {
    if (!this.isAdmin) {
      this.ns.error('Admin-Rechte erforderlich');
      return;
    }
    if (!this.hub || !confirm('Vorlage wirklich loeschen?')) return;
    this.hubApi.deleteTemplate(this.hub.url, id).subscribe({
      next: (res) => {
        this.items = this.items.filter((t) => t.id !== id);
        this.ns.success('Geloescht');
        const cleared = res?.cleared;
        if (cleared && (cleared.roles?.length || cleared.team_type_links?.length || cleared.team_members?.length || cleared.teams?.length)) {
          const parts = [];
          if (cleared.roles?.length) parts.push(`Rollen: ${cleared.roles.length}`);
          if (cleared.team_type_links?.length) parts.push(`Team-Typen: ${cleared.team_type_links.length}`);
          if (cleared.team_members?.length) parts.push(`Team-Mitglieder: ${cleared.team_members.length}`);
          if (cleared.teams?.length) parts.push(`Teams: ${cleared.teams.length}`);
          if (parts.length) this.ns.info(`Referenzen entfernt (${parts.join(', ')})`);
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
      },
    });
  }
}
