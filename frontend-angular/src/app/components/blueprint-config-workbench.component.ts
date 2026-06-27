import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { forkJoin, from, of } from 'rxjs';
import { catchError, concatMap, finalize, switchMap, toArray } from 'rxjs/operators';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { AdminFacade } from '../features/admin/admin.facade';

type BlueprintRoleForm = {
  name: string;
  description: string;
  template_id: string;
  sort_order: number;
  is_required: boolean;
  preferred_backend: string;
  configText: string;
};

type BlueprintArtifactForm = {
  kind: string;
  title: string;
  description: string;
  sort_order: number;
  payloadText: string;
};

type TemplateForm = {
  id: string;
  name: string;
  description: string;
  prompt_template: string;
  dirty: boolean;
};

@Component({
  standalone: true,
  selector: 'app-blueprint-config-workbench',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="bcw-root">
      <div class="bcw-header">
        <div>
          <h2>Blueprint-Konfiguration</h2>
          <p>Blueprint, Rollen, Templates, Artefakte, Work-Profile und Roh-Konfig gemeinsam bearbeiten.</p>
        </div>
        <div class="header-actions">
          <button class="secondary" (click)="reload()" [disabled]="loading">Aktualisieren</button>
          <button (click)="saveBlueprintAndTemplates()" [disabled]="!selectedBlueprint || saving">Speichern</button>
          <button class="secondary" (click)="startClone()" [disabled]="!selectedBlueprint || saving">Klonen</button>
        </div>
      </div>
    
      @if (error) {
        <div class="bcw-status error">{{ error }}</div>
      }
      @if (loading) {
        <div class="bcw-status">Lade Blueprint-Konfiguration...</div>
      }
    
      @if (!loading) {
        <div class="bcw-layout">
          <aside class="blueprint-list">
            <label>
              <span>Blueprints</span>
              <input [(ngModel)]="filterText" placeholder="Suchen..." />
            </label>
            @for (blueprint of filteredBlueprints(); track blueprint) {
              <button
                class="blueprint-item"
                [class.active]="blueprint.id === selectedBlueprintId"
                (click)="selectBlueprint(blueprint.id)">
                <strong>{{ blueprint.name }}</strong>
                <small>{{ blueprint.roles?.length || 0 }} Rollen · {{ blueprint.artifacts?.length || 0 }} Artefakte</small>
              </button>
            }
          </aside>
          @if (selectedBlueprint) {
            <main class="workbench-main">
              <section class="summary-band">
                <div>
                  <label>Name</label>
                  <input [(ngModel)]="blueprintForm.name" />
                </div>
                <div>
                  <label>Basis-Teamtyp</label>
                  <input [(ngModel)]="blueprintForm.base_team_type_name" placeholder="optional" />
                </div>
                <div class="summary-wide">
                  <label>Beschreibung</label>
                  <textarea rows="2" [(ngModel)]="blueprintForm.description"></textarea>
                </div>
              </section>
              @if (cloneOpen) {
                <section class="clone-panel">
                  <div>
                    <label>Neue Blueprint-Variante</label>
                    <input [(ngModel)]="cloneName" placeholder="z.B. Repair Blueprint - FreeCAD" />
                  </div>
                  <label class="checkbox-row">
                    <input type="checkbox" [(ngModel)]="cloneTemplates" />
                    Zugehörige Templates als eigene bearbeitbare Kopien klonen
                  </label>
                  <div class="row-actions">
                    <button (click)="cloneBlueprint()" [disabled]="saving || !cloneName.trim()">Klon speichern</button>
                    <button class="secondary" (click)="cloneOpen = false">Abbrechen</button>
                  </div>
                </section>
              }
              <nav class="tabs">
                <button [class.active]="activeTab === 'roles'" (click)="activeTab = 'roles'">Rollen + Templates</button>
                <button [class.active]="activeTab === 'artifacts'" (click)="activeTab = 'artifacts'">Artefakte</button>
                <button [class.active]="activeTab === 'profile'" (click)="activeTab = 'profile'">Work-Profile</button>
                <button [class.active]="activeTab === 'raw'" (click)="activeTab = 'raw'">Roh-Konfig</button>
              </nav>
              @if (activeTab === 'roles') {
                <section class="tab-panel">
                  <div class="section-head">
                    <h3>Rollen und zugehörige Templates</h3>
                    <button class="secondary" (click)="addRole()">Rolle hinzufügen</button>
                  </div>
                  <div class="role-grid">
                    @for (role of roles; track role; let index = $index) {
                      <article class="role-card">
                        <div class="role-head">
                          <input [(ngModel)]="role.name" placeholder="Rollenname" />
                          <button class="danger" (click)="removeRole(index)">Entfernen</button>
                        </div>
                        <textarea rows="2" [(ngModel)]="role.description" placeholder="Rollenbeschreibung"></textarea>
                        <div class="role-controls">
                          <label>
                            <span>Template</span>
                            <select [(ngModel)]="role.template_id" (ngModelChange)="syncTemplateSelection()">
                              <option value="">Kein Template</option>
                              @for (tpl of templates; track tpl) {
                                <option [value]="tpl.id">{{ tpl.name }}</option>
                              }
                            </select>
                          </label>
                          <label>
                            <span>Bevorzugtes Backend</span>
                            <select [(ngModel)]="role.preferred_backend">
                              <option value="">Standard (Hub-Default)</option>
                              <option value="ananta-worker">ananta-worker</option>
                              <option value="deerflow">deerflow</option>
                              <option value="codex">codex</option>
                              <option value="opencode">opencode</option>
                              <option value="aider">aider</option>
                              <option value="sgpt">sgpt</option>
                            </select>
                          </label>
                          <label>
                            <span>Sortierung</span>
                            <input type="number" [(ngModel)]="role.sort_order" />
                          </label>
                          <label class="checkbox-row">
                            <input type="checkbox" [(ngModel)]="role.is_required" />
                            Pflichtrolle
                          </label>
                        </div>
                        <label>
                          <span>Rollen-Konfig JSON</span>
                          <textarea rows="7" [(ngModel)]="role.configText"></textarea>
                        </label>
                      </article>
                    }
                  </div>
                  @if (templateForms.length) {
                    <div class="template-editor">
                      <h3>Zugehörige Template-Inhalte</h3>
                      @for (tpl of templateForms; track tpl) {
                        <article class="template-card">
                          <div class="template-head">
                            <input [(ngModel)]="tpl.name" (ngModelChange)="tpl.dirty = true" />
                            <span>{{ rolesUsingTemplate(tpl.id).join(', ') }}</span>
                          </div>
                          <input [(ngModel)]="tpl.description" (ngModelChange)="tpl.dirty = true" placeholder="Template-Beschreibung" />
                          <textarea rows="12" [(ngModel)]="tpl.prompt_template" (ngModelChange)="tpl.dirty = true"></textarea>
                        </article>
                      }
                    </div>
                  }
                </section>
              }
              @if (activeTab === 'artifacts') {
                <section class="tab-panel">
                  <div class="section-head">
                    <h3>Blueprint-Artefakte</h3>
                    <button class="secondary" (click)="addArtifact()">Artefakt hinzufügen</button>
                  </div>
                  @for (artifact of artifacts; track artifact; let index = $index) {
                    <article class="artifact-card">
                      <div class="artifact-row">
                        <select [(ngModel)]="artifact.kind">
                          <option value="task">Task</option>
                          <option value="policy">Policy</option>
                        </select>
                        <input [(ngModel)]="artifact.title" placeholder="Titel" />
                        <input type="number" [(ngModel)]="artifact.sort_order" />
                        <button class="danger" (click)="removeArtifact(index)">Entfernen</button>
                      </div>
                      <textarea rows="2" [(ngModel)]="artifact.description" placeholder="Beschreibung"></textarea>
                      <label>
                        <span>Payload JSON</span>
                        <textarea rows="6" [(ngModel)]="artifact.payloadText"></textarea>
                      </label>
                    </article>
                  }
                </section>
              }
              @if (activeTab === 'profile') {
                <section class="tab-panel profile-panel">
                  <div>
                    <h3>Work-Profile</h3>
                    <pre>{{ workProfile | json }}</pre>
                  </div>
                  <div>
                    <h3>Bundle</h3>
                    <pre>{{ bundle | json }}</pre>
                  </div>
                </section>
              }
              @if (activeTab === 'raw') {
                <section class="tab-panel raw-panel">
                  <h3>Gesamte bearbeitete Blueprint-Konfig</h3>
                  <pre>{{ buildBlueprintPayload() | json }}</pre>
                </section>
              }
            </main>
          } @else {
            <main class="empty-state">Blueprint auswählen, um die vollständige Konfiguration zu bearbeiten.</main>
          }
        </div>
      }
    
    </div>
    `,
  styles: [`
    .bcw-root { min-height: calc(100vh - 150px); background: var(--bg); color: var(--fg); }
    .bcw-header { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; padding:18px 22px; border-bottom:1px solid var(--border); }
    .bcw-header h2 { margin:0 0 4px; font-size:22px; }
    .bcw-header p { margin:0; color:var(--muted); max-width:760px; }
    .header-actions, .row-actions, .role-head, .template-head, .artifact-row, .section-head { display:flex; gap:8px; align-items:center; }
    .section-head { justify-content:space-between; margin-bottom:10px; }
    .bcw-layout { display:grid; grid-template-columns:280px minmax(0, 1fr); min-height:calc(100vh - 230px); }
    .blueprint-list { border-right:1px solid var(--border); padding:14px; display:flex; flex-direction:column; gap:8px; overflow:auto; }
    .blueprint-list label, .summary-band label, .role-card label, .artifact-card label, .clone-panel label { display:flex; flex-direction:column; gap:5px; font-size:12px; color:var(--muted); }
    .blueprint-item { text-align:left; border:1px solid var(--border); border-radius:8px; padding:10px; background:var(--card-bg); color:var(--fg); }
    .blueprint-item.active { border-color:var(--accent); background:color-mix(in srgb, var(--accent) 12%, var(--card-bg)); }
    .blueprint-item strong, .blueprint-item small { display:block; }
    .blueprint-item small { margin-top:4px; color:var(--muted); }
    .workbench-main { min-width:0; padding:16px; overflow:auto; }
    .summary-band { display:grid; grid-template-columns:1fr 240px; gap:12px; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card-bg); }
    .summary-wide { grid-column:1 / -1; }
    input, textarea, select { width:100%; box-sizing:border-box; border:1px solid var(--border); border-radius:6px; background:var(--input-bg, var(--bg)); color:var(--fg); padding:8px; font:inherit; }
    textarea { resize:vertical; font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:12px; }
    .clone-panel { margin-top:12px; border:1px solid var(--accent); border-radius:8px; padding:12px; background:color-mix(in srgb, var(--accent) 8%, var(--card-bg)); display:grid; gap:10px; }
    .checkbox-row { flex-direction:row !important; align-items:center; color:var(--fg) !important; }
    .checkbox-row input { width:auto; }
    .tabs { display:flex; gap:6px; margin:14px 0; border-bottom:1px solid var(--border); }
    .tabs button { border:none; border-bottom:2px solid transparent; border-radius:0; background:transparent; color:var(--muted); padding:9px 12px; }
    .tabs button.active { color:var(--fg); border-bottom-color:var(--accent); }
    .tab-panel { display:block; }
    .role-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(340px, 1fr)); gap:12px; }
    .role-card, .template-card, .artifact-card { border:1px solid var(--border); border-radius:8px; background:var(--card-bg); padding:12px; display:grid; gap:10px; }
    .role-head input { font-weight:700; }
    .role-controls { display:grid; grid-template-columns:1fr 90px 110px; gap:10px; align-items:end; }
    .template-editor { margin-top:18px; display:grid; gap:12px; }
    .template-head { justify-content:space-between; }
    .template-head span { color:var(--muted); font-size:12px; white-space:nowrap; }
    .artifact-row { grid-template-columns:120px 1fr 90px auto; }
    .profile-panel { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    pre { margin:0; border:1px solid var(--border); border-radius:8px; background:var(--card-bg); padding:12px; overflow:auto; max-height:620px; font-size:12px; }
    .bcw-status { margin:12px 16px; border:1px solid var(--border); border-radius:8px; padding:10px 12px; background:var(--card-bg); }
    .bcw-status.error { border-color:#c62828; color:#ff8a80; }
    .empty-state { padding:28px; color:var(--muted); }
    .danger { border-color:#8e2424; color:#ff8a80; background:transparent; }
    @media (max-width: 900px) {
      .bcw-header { flex-direction:column; }
      .bcw-layout, .summary-band, .profile-panel { grid-template-columns:1fr; }
      .role-controls, .artifact-row { grid-template-columns:1fr; }
    }
  `],
})
export class BlueprintConfigWorkbenchComponent implements OnInit {
  private dir = inject(AgentDirectoryService);
  private api = inject(AdminFacade);
  private ns = inject(NotificationService);

  hub = this.dir.list().find(agent => agent.role === 'hub');
  loading = false;
  saving = false;
  error = '';
  filterText = '';
  activeTab: 'roles' | 'artifacts' | 'profile' | 'raw' = 'roles';

  blueprints: any[] = [];
  templates: any[] = [];
  selectedBlueprintId = '';
  selectedBlueprint: any = null;
  workProfile: any = null;
  bundle: any = null;

  blueprintForm = { name: '', description: '', base_team_type_name: '' };
  roles: BlueprintRoleForm[] = [];
  artifacts: BlueprintArtifactForm[] = [];
  templateForms: TemplateForm[] = [];

  cloneOpen = false;
  cloneName = '';
  cloneTemplates = true;

  ngOnInit(): void {
    this.reload();
  }

  reload(): void {
    this.hub = this.dir.list().find(agent => agent.role === 'hub');
    if (!this.hub) {
      this.error = 'Kein Hub-Agent konfiguriert.';
      return;
    }
    this.loading = true;
    this.error = '';
    forkJoin({
      blueprints: this.api.listBlueprints(this.hub.url).pipe(catchError(() => of([]))),
      templates: this.api.listTemplates(this.hub.url).pipe(catchError(() => of([]))),
    }).pipe(finalize(() => (this.loading = false))).subscribe({
      next: ({ blueprints, templates }) => {
        this.blueprints = Array.isArray(blueprints) ? blueprints : [];
        this.templates = Array.isArray(templates) ? templates : [];
        const nextId = this.selectedBlueprintId || this.blueprints[0]?.id || '';
        if (nextId) this.selectBlueprint(nextId);
      },
      error: err => {
        this.error = err?.message || 'Blueprint-Konfiguration konnte nicht geladen werden.';
      },
    });
  }

  filteredBlueprints(): any[] {
    const q = this.filterText.trim().toLowerCase();
    if (!q) return this.blueprints;
    return this.blueprints.filter(item => `${item.name || ''} ${item.description || ''}`.toLowerCase().includes(q));
  }

  selectBlueprint(id: string): void {
    if (!this.hub || !id) return;
    this.selectedBlueprintId = id;
    this.error = '';
    forkJoin({
      blueprint: this.api.getBlueprint(this.hub.url, id),
      workProfile: this.api.getBlueprintWorkProfile(this.hub.url, id).pipe(catchError(() => of(null))),
      bundle: this.api.exportBlueprintBundle(this.hub.url, id).pipe(catchError(() => of(null))),
    }).subscribe({
      next: ({ blueprint, workProfile, bundle }) => {
        this.selectedBlueprint = blueprint;
        this.workProfile = workProfile;
        this.bundle = bundle;
        this.hydrateForms(blueprint);
      },
      error: err => {
        this.error = err?.message || 'Blueprint konnte nicht geladen werden.';
      },
    });
  }

  hydrateForms(blueprint: any): void {
    this.blueprintForm = {
      name: blueprint?.name || '',
      description: blueprint?.description || '',
      base_team_type_name: blueprint?.base_team_type_name || '',
    };
    this.roles = [...(blueprint?.roles || [])]
      .sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0))
      .map(role => ({
        name: role.name || '',
        description: role.description || '',
        template_id: role.template_id || '',
        sort_order: Number(role.sort_order || 0),
        is_required: role.is_required !== false,
        preferred_backend: String((role.config || {}).preferred_backend || ''),
        configText: this.toPrettyJson(role.config || {}),
      }));
    this.artifacts = [...(blueprint?.artifacts || [])]
      .sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0))
      .map(artifact => ({
        kind: artifact.kind || 'task',
        title: artifact.title || '',
        description: artifact.description || '',
        sort_order: Number(artifact.sort_order || 0),
        payloadText: this.toPrettyJson(artifact.payload || {}),
      }));
    this.syncTemplateSelection();
  }

  syncTemplateSelection(): void {
    const templateIds = new Set(this.roles.map(role => role.template_id).filter(Boolean));
    this.templateForms = this.templates
      .filter(template => templateIds.has(template.id))
      .map(template => {
        const existing = this.templateForms.find(item => item.id === template.id);
        return existing || {
          id: template.id,
          name: template.name || '',
          description: template.description || '',
          prompt_template: template.prompt_template || '',
          dirty: false,
        };
      });
  }

  rolesUsingTemplate(templateId: string): string[] {
    return this.roles.filter(role => role.template_id === templateId).map(role => role.name).filter(Boolean);
  }

  addRole(): void {
    this.roles.push({
      name: 'Neue Rolle',
      description: '',
      template_id: '',
      sort_order: this.roles.length + 1,
      is_required: true,
      preferred_backend: '',
      configText: '{}',
    });
  }

  removeRole(index: number): void {
    this.roles.splice(index, 1);
    this.syncTemplateSelection();
  }

  addArtifact(): void {
    this.artifacts.push({
      kind: 'task',
      title: 'Neues Artefakt',
      description: '',
      sort_order: this.artifacts.length + 1,
      payloadText: '{\n  "status": "todo",\n  "priority": "Medium"\n}',
    });
  }

  removeArtifact(index: number): void {
    this.artifacts.splice(index, 1);
  }

  buildBlueprintPayload(): any {
    return {
      name: this.blueprintForm.name.trim(),
      description: this.blueprintForm.description || '',
      base_team_type_name: this.blueprintForm.base_team_type_name || null,
      roles: this.roles.map(role => {
        const config = this.parseJson(role.configText, {});
        if (role.preferred_backend) {
          config['preferred_backend'] = role.preferred_backend;
        } else {
          delete config['preferred_backend'];
        }
        return {
          name: role.name.trim(),
          description: role.description || '',
          template_id: role.template_id || null,
          sort_order: Number(role.sort_order || 0),
          is_required: role.is_required !== false,
          config,
        };
      }),
      artifacts: this.artifacts.map(artifact => ({
        kind: artifact.kind || 'task',
        title: artifact.title.trim(),
        description: artifact.description || '',
        sort_order: Number(artifact.sort_order || 0),
        payload: this.parseJson(artifact.payloadText, {}),
      })),
    };
  }

  saveBlueprintAndTemplates(): void {
    if (!this.hub || !this.selectedBlueprint) return;
    const validation = this.validateForms();
    if (validation) {
      this.error = validation;
      return;
    }
    this.saving = true;
    this.error = '';
    const templateUpdates = this.templateForms
      .filter(template => template.dirty)
      .map(template => this.api.updateTemplate(this.hub!.url, template.id, {
        name: template.name.trim(),
        description: template.description || '',
        prompt_template: template.prompt_template || '',
      }));
    forkJoin(templateUpdates.length ? templateUpdates : [of(null)]).pipe(
      switchMap(() => this.api.patchBlueprint(this.hub!.url, this.selectedBlueprint.id, this.buildBlueprintPayload())),
      finalize(() => (this.saving = false)),
    ).subscribe({
      next: saved => {
        this.ns.success('Blueprint-Konfiguration gespeichert');
        this.selectedBlueprint = saved;
        this.upsertBlueprint(saved);
        this.hydrateForms(saved);
      },
      error: err => {
        this.error = err?.error?.message || err?.message || 'Speichern fehlgeschlagen.';
      },
    });
  }

  startClone(): void {
    this.cloneOpen = true;
    this.cloneName = `${this.blueprintForm.name} Kopie`.trim();
  }

  cloneBlueprint(): void {
    if (!this.hub || !this.selectedBlueprint) return;
    const validation = this.validateForms();
    if (validation) {
      this.error = validation;
      return;
    }
    this.saving = true;
    this.error = '';
    const sourcePayload = this.buildBlueprintPayload();
    sourcePayload.name = this.cloneName.trim();
    const templateCloneRequests = this.cloneTemplates
      ? this.templateForms.map(template => ({
          sourceId: template.id,
          request: this.api.createTemplate(this.hub!.url, {
            name: this.uniqueTemplateName(`${sourcePayload.name} / ${template.name}`),
            description: template.description || '',
            prompt_template: template.prompt_template || '',
          }),
        }))
      : [];

    from(templateCloneRequests).pipe(
      concatMap(item => item.request.pipe(
        catchError(err => {
          throw new Error(err?.error?.message || err?.message || `Template-Klon fehlgeschlagen: ${item.sourceId}`);
        }),
        switchMap(created => of({ sourceId: item.sourceId, created })),
      )),
      toArray(),
      switchMap(createdTemplates => {
        const templateIdMap = new Map(createdTemplates.map(item => [item.sourceId, item.created.id]));
        const clonedPayload = {
          ...sourcePayload,
          roles: sourcePayload.roles.map((role: any) => ({
            ...role,
            template_id: templateIdMap.get(role.template_id) || role.template_id || null,
          })),
        };
        return this.api.createBlueprint(this.hub!.url, clonedPayload);
      }),
      finalize(() => (this.saving = false)),
    ).subscribe({
      next: created => {
        this.ns.success('Blueprint-Variante geklont');
        this.cloneOpen = false;
        this.blueprints = [...this.blueprints, created];
        this.reload();
        this.selectedBlueprintId = created.id;
      },
      error: err => {
        this.error = err?.message || 'Klonen fehlgeschlagen.';
      },
    });
  }

  validateForms(): string {
    if (!this.blueprintForm.name.trim()) return 'Blueprint-Name fehlt.';
    for (const role of this.roles) {
      if (!role.name.trim()) return 'Eine Rolle hat keinen Namen.';
      const parsed = this.safeParseJson(role.configText);
      if (!parsed.ok) return `Rollen-Konfig ist kein gültiges JSON: ${role.name}`;
    }
    for (const artifact of this.artifacts) {
      if (!artifact.title.trim()) return 'Ein Artefakt hat keinen Titel.';
      const parsed = this.safeParseJson(artifact.payloadText);
      if (!parsed.ok) return `Artefakt-Payload ist kein gültiges JSON: ${artifact.title}`;
    }
    return '';
  }

  private uniqueTemplateName(baseName: string): string {
    const existing = new Set(this.templates.map(template => String(template.name || '').trim().toLowerCase()));
    let candidate = baseName.trim();
    let index = 2;
    while (existing.has(candidate.toLowerCase())) {
      candidate = `${baseName.trim()} ${index}`;
      index += 1;
    }
    return candidate;
  }

  private upsertBlueprint(saved: any): void {
    const index = this.blueprints.findIndex(item => item.id === saved.id);
    if (index >= 0) this.blueprints[index] = saved;
    else this.blueprints.push(saved);
  }

  private toPrettyJson(value: any): string {
    return JSON.stringify(value ?? {}, null, 2);
  }

  private parseJson(text: string, fallback: any): any {
    const parsed = this.safeParseJson(text);
    return parsed.ok ? parsed.value : fallback;
  }

  private safeParseJson(text: string): { ok: true; value: any } | { ok: false } {
    try {
      return { ok: true, value: JSON.parse(text || '{}') };
    } catch {
      return { ok: false };
    }
  }
}
