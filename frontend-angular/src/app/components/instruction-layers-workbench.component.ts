import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { ToastService } from '../services/toast.service';

interface ProfileExample {
  id: string;
  name: string;
  description?: string;
  prompt_content?: string;
  profile_metadata?: any;
}

interface OverlayPreset {
  id: string;
  title: string;
  prompt: string;
  scope: string;
  attachment_kind: string;
}

@Component({
  standalone: true,
  selector: 'app-instruction-layers-workbench',
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="container pb-lg">
      <div class="row space-between">
        <div>
          <h2 class="no-margin">Instruction Layers</h2>
          <p class="muted mt-sm">Profile steuern den dauerhaften Arbeitsstil. Overlays steuern task-/goal-/session-spezifische Zusatzanweisungen.</p>
        </div>
        <button class="secondary btn-small" type="button" (click)="refreshData()">Refresh</button>
      </div>

      @if (showFirstRunHint) {
        <div class="state-banner mt-md inline-help">
          <strong>Erststart-Hinweis</strong>
          <p class="muted no-margin mt-sm">
            Profile sind persistent und eignen sich fuer deinen Standardstil. Overlays sind bewusst temporaer und fuer konkrete Task-/Goal-/Session-Kontexte.
            Governance bleibt immer dominant.
          </p>
          <button class="secondary btn-small mt-sm" type="button" (click)="dismissFirstRunHint()">Verstanden</button>
        </div>
      }

      @if (!hubUrl) {
        <div class="card mt-md">
          <p class="muted no-margin">Kein Hub gefunden.</p>
        </div>
      } @else {
        <div class="grid cols-2 gap-md mt-md">
          <div class="card">
            <div class="row space-between">
              <h3 class="no-margin">Profile verwalten</h3>
              <button class="secondary btn-small" type="button" (click)="resetProfileForm()">Neu</button>
            </div>
            <div class="grid gap-sm mt-sm">
              <label>Preset
                <select [(ngModel)]="selectedProfilePresetId" (ngModelChange)="applyProfilePreset($event)">
                  <option value="">Preset auswaehlen</option>
                  @for (example of profileExamples; track example.id) {
                    <option [value]="example.id">{{ example.name }}</option>
                  }
                </select>
              </label>
              <label>Name
                <input [(ngModel)]="profileForm.name" placeholder="z.B. review-first" />
              </label>
              <label>Prompt
                <textarea [(ngModel)]="profileForm.prompt_content" rows="4" placeholder="Persistenter Arbeitsstil"></textarea>
              </label>
              <div class="grid cols-2 gap-sm">
                <label>Working mode
                  <select [(ngModel)]="profileForm.working_mode">
                    <option value="">-</option>
                    <option value="implementation">implementation</option>
                    <option value="review">review</option>
                    <option value="research">research</option>
                  </select>
                </label>
                <label>Style
                  <select [(ngModel)]="profileForm.style">
                    <option value="">-</option>
                    <option value="concise">concise</option>
                    <option value="detailed">detailed</option>
                  </select>
                </label>
                <label>Language
                  <input [(ngModel)]="profileForm.language" placeholder="de / en" />
                </label>
                <label>Detail level
                  <select [(ngModel)]="profileForm.detail_level">
                    <option value="">-</option>
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                  </select>
                </label>
              </div>
              <label>Blockierte Template-Kontexte (CSV, optional)
                <input [(ngModel)]="profileForm.blocked_template_contexts" placeholder="z.B. review" />
              </label>
              <div class="row gap-sm">
                <label class="row gap-sm"><input type="checkbox" [(ngModel)]="profileForm.is_active" /> aktiv</label>
                <label class="row gap-sm"><input type="checkbox" [(ngModel)]="profileForm.is_default" /> default</label>
              </div>
              <div class="row gap-sm">
                <button type="button" (click)="saveProfile()" [disabled]="busy">Speichern</button>
                @if (editingProfileId) {
                  <button class="secondary" type="button" (click)="resetProfileForm()">Abbrechen</button>
                }
              </div>
            </div>
            <div class="grid gap-sm mt-md">
              @for (profile of profiles; track profile.id) {
                <div class="card card-light">
                  <div class="row space-between">
                    <strong>{{ profile.name }}</strong>
                    <div class="row gap-sm">
                      @if (profile.is_default) { <span class="badge success">default</span> }
                      @if (!profile.is_active) { <span class="badge warning">inaktiv</span> }
                    </div>
                  </div>
                  <p class="muted mt-sm">{{ profile.prompt_content }}</p>
                  <div class="row gap-sm mt-sm">
                    <button class="secondary btn-small" type="button" (click)="editProfile(profile)">Bearbeiten</button>
                    <button class="secondary btn-small" type="button" (click)="selectDefaultProfile(profile.id)">Als default</button>
                    <button class="secondary btn-small" type="button" (click)="toggleProfileActive(profile)">{{ profile.is_active ? 'Deaktivieren' : 'Aktivieren' }}</button>
                    <button class="danger btn-small" type="button" (click)="deleteProfile(profile.id)">Loeschen</button>
                  </div>
                </div>
              }
              @if (!profiles.length) {
                <p class="muted no-margin">Noch keine Profile vorhanden.</p>
              }
            </div>
          </div>

          <div class="card">
            <div class="row space-between">
              <h3 class="no-margin">Overlays verwalten</h3>
              <button class="secondary btn-small" type="button" (click)="resetOverlayForm()">Neu</button>
            </div>
            <div class="grid gap-sm mt-sm">
              <label>Preset
                <select [(ngModel)]="selectedOverlayPresetId" (ngModelChange)="applyOverlayPreset($event)">
                  <option value="">Preset auswaehlen</option>
                  @for (preset of overlayPresets; track preset.id) {
                    <option [value]="preset.id">{{ preset.title }}</option>
                  }
                </select>
              </label>
              <label>Name
                <input [(ngModel)]="overlayForm.name" placeholder="z.B. session-review-overlay" />
              </label>
              <label>Prompt
                <textarea [(ngModel)]="overlayForm.prompt_content" rows="4" placeholder="Scoped Zusatzanweisung"></textarea>
              </label>
              <div class="grid cols-2 gap-sm">
                <label>Scope
                  <select [(ngModel)]="overlayForm.scope">
                    @for (scope of overlayScopes; track scope) {
                      <option [value]="scope">{{ scope }}</option>
                    }
                  </select>
                </label>
                <label>Attachment kind
                  <select [(ngModel)]="overlayForm.attachment_kind">
                    <option value="">-</option>
                    @for (kind of overlayAttachmentKinds; track kind) {
                      <option [value]="kind">{{ kind }}</option>
                    }
                  </select>
                </label>
              </div>
              <label>Attachment id
                <input [(ngModel)]="overlayForm.attachment_id" placeholder="task-id / goal-id / session-id / usage-key" />
              </label>
              <label>Working mode
                <select [(ngModel)]="overlayForm.working_mode">
                  <option value="">-</option>
                  <option value="implementation">implementation</option>
                  <option value="review">review</option>
                  <option value="research">research</option>
                </select>
              </label>
              <label>Max uses (nur one_shot, optional)
                <input type="number" min="1" [(ngModel)]="overlayForm.max_uses" />
              </label>
              <label class="row gap-sm"><input type="checkbox" [(ngModel)]="overlayForm.is_active" /> aktiv</label>
              <div class="row gap-sm">
                <button type="button" (click)="saveOverlay()" [disabled]="busy">Speichern</button>
                @if (editingOverlayId) {
                  <button class="secondary" type="button" (click)="resetOverlayForm()">Abbrechen</button>
                }
              </div>
            </div>
            <div class="grid gap-sm mt-md">
              @for (overlay of overlays; track overlay.id) {
                <div class="card card-light">
                  <div class="row space-between">
                    <strong>{{ overlay.name }}</strong>
                    <div class="row gap-sm">
                      <span class="badge">{{ overlay.scope }}</span>
                      @if (!overlay.is_active) { <span class="badge warning">inaktiv</span> }
                    </div>
                  </div>
                  <p class="muted mt-sm">{{ overlay.prompt_content }}</p>
                  <div class="muted font-sm">
                    Bindung: {{ overlay.attachment_kind || '-' }} / {{ overlay.attachment_id || '-' }}
                  </div>
                  @if (overlay.lifecycle) {
                    <div class="muted font-sm">Lifecycle: {{ overlay.lifecycle.kind }} | remaining: {{ overlay.lifecycle.remaining_uses !== null && overlay.lifecycle.remaining_uses !== undefined ? overlay.lifecycle.remaining_uses : 'n/a' }}</div>
                  }
                  <div class="row gap-sm mt-sm">
                    <button class="secondary btn-small" type="button" (click)="editOverlay(overlay)">Bearbeiten</button>
                    <button class="secondary btn-small" type="button" (click)="selectOverlay(overlay.id)">Aktivieren</button>
                    <button class="secondary btn-small" type="button" (click)="detachOverlay(overlay.id)">Detach</button>
                    <button class="danger btn-small" type="button" (click)="deleteOverlay(overlay.id)">Loeschen</button>
                  </div>
                </div>
              }
              @if (!overlays.length) {
                <p class="muted no-margin">Noch keine Overlays vorhanden.</p>
              }
            </div>
          </div>
        </div>

        <div class="grid cols-2 gap-md mt-md">
          <div class="card">
            <h3 class="no-margin">Task/Goal Reuse Flows</h3>
            <p class="muted mt-sm">Bestehende Tasks oder Sessions koennen ein vorhandenes Overlay explizit wiederverwenden.</p>
            <div class="grid gap-sm">
              <label>Task ID
                <input [(ngModel)]="selectionForm.task_id" placeholder="task-id" />
              </label>
              <label>Goal ID
                <input [(ngModel)]="selectionForm.goal_id" placeholder="goal-id" />
              </label>
              <label>Profil
                <select [(ngModel)]="selectionForm.profile_id">
                  <option value="">-</option>
                  @for (profile of profiles; track profile.id) {
                    <option [value]="profile.id">{{ profile.name }}</option>
                  }
                </select>
              </label>
              <label>Overlay
                <select [(ngModel)]="selectionForm.overlay_id">
                  <option value="">-</option>
                  @for (overlay of overlays; track overlay.id) {
                    <option [value]="overlay.id">{{ overlay.name }}</option>
                  }
                </select>
              </label>
              <div class="row gap-sm">
                <button type="button" (click)="applyTaskSelection()" [disabled]="!selectionForm.task_id">Task-Auswahl speichern</button>
                <button class="secondary" type="button" (click)="applyGoalSelection()" [disabled]="!selectionForm.goal_id">Goal-Auswahl speichern</button>
              </div>
            </div>
            @if (lastSelectionResult) {
              <div class="card card-light mt-sm">
                <div class="muted font-sm">Letzte Auswahl gespeichert</div>
                <strong>{{ lastSelectionResult.owner_username || '-' }}</strong>
              </div>
            }
          </div>

          <div class="card">
            <h3 class="no-margin">Session/Usage Reuse</h3>
            <div class="grid gap-sm mt-sm">
              <label>Overlay
                <select [(ngModel)]="reuseForm.overlay_id">
                  <option value="">-</option>
                  @for (overlay of overlays; track overlay.id) {
                    <option [value]="overlay.id">{{ overlay.name }}</option>
                  }
                </select>
              </label>
              <label>Session ID
                <input [(ngModel)]="reuseForm.session_id" placeholder="session-id" />
              </label>
              <label>Usage key
                <input [(ngModel)]="reuseForm.usage_key" placeholder="project:my-key" />
              </label>
              <div class="row gap-sm">
                <button type="button" (click)="attachOverlayToSession()" [disabled]="!reuseForm.overlay_id || !reuseForm.session_id">An Session binden</button>
                <button class="secondary" type="button" (click)="attachOverlayToUsage()" [disabled]="!reuseForm.overlay_id || !reuseForm.usage_key">An Usage binden</button>
              </div>
            </div>
          </div>
        </div>

        <div class="card mt-md">
          <div class="row space-between">
            <h3 class="no-margin">Effective Instruction Stack Preview</h3>
            <button class="secondary btn-small" type="button" (click)="previewEffective()">Neu berechnen</button>
          </div>
          <div class="grid cols-3 gap-sm mt-sm">
            <label>Task ID
              <input [(ngModel)]="effectiveForm.task_id" placeholder="task-id" />
            </label>
            <label>Goal ID
              <input [(ngModel)]="effectiveForm.goal_id" placeholder="goal-id" />
            </label>
            <label>Session ID
              <input [(ngModel)]="effectiveForm.session_id" placeholder="session-id" />
            </label>
            <label>Usage key
              <input [(ngModel)]="effectiveForm.usage_key" placeholder="project:key" />
            </label>
            <label>Profil override
              <select [(ngModel)]="effectiveForm.profile_id">
                <option value="">-</option>
                @for (profile of profiles; track profile.id) {
                  <option [value]="profile.id">{{ profile.name }}</option>
                }
              </select>
            </label>
            <label>Overlay override
              <select [(ngModel)]="effectiveForm.overlay_id">
                <option value="">-</option>
                @for (overlay of overlays; track overlay.id) {
                  <option [value]="overlay.id">{{ overlay.name }}</option>
                }
              </select>
            </label>
          </div>
          <div class="row gap-sm mt-sm">
            <button type="button" (click)="previewEffective()" [disabled]="busy">Preview</button>
            <a [routerLink]="['/dashboard']" class="button-outline">Zurueck zum Dashboard</a>
          </div>

          @if (effectiveError) {
            <div class="state-banner warning mt-sm">
              <strong>Preview fehlgeschlagen</strong>
              <p class="muted no-margin mt-sm">{{ effectiveError }}</p>
            </div>
          }
          @if (effectivePreview?.diagnostics; as diagnostics) {
            <div class="grid cols-2 gap-sm mt-md">
              <div class="card card-light">
                <div class="muted font-sm">Aktives Profil</div>
                <strong>{{ diagnostics.selected_profile?.name || '-' }}</strong>
                <div class="muted font-sm mt-sm">Aktives Overlay</div>
                <strong>{{ diagnostics.selected_overlay?.name || '-' }}</strong>
              </div>
              <div class="card card-light">
                <div class="muted font-sm">Precedence</div>
                <strong>{{ (diagnostics.precedence || []).join(' > ') }}</strong>
              </div>
            </div>
            @if (diagnostics.template_compatibility; as compat) {
              <div class="card card-light mt-sm">
                <div class="row space-between">
                  <strong>Role/Template Compatibility</strong>
                  <span class="badge" [class.success]="compat.status === 'ok'" [class.warning]="compat.status === 'warn'" [class.danger]="compat.status === 'block'">
                    {{ compat.status }}
                  </span>
                </div>
                <div class="muted font-sm mt-sm">
                  Role: {{ compat.role_template_context?.role_name || '-' }} | Template: {{ compat.role_template_context?.template_name || '-' }}
                </div>
                @if (compat.issues?.length) {
                  <ul class="mt-sm">
                    @for (issue of compat.issues; track issue.code + issue.layer_id) {
                      <li>{{ issue.severity }}: {{ issue.message }}</li>
                    }
                  </ul>
                }
              </div>
            }
          }
        </div>
      }
    </div>
  `,
})
export class InstructionLayersWorkbenchComponent implements OnInit {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);
  private toast = inject(ToastService);

  hubUrl = this.dir.list().find(agent => agent.role === 'hub')?.url || '';
  busy = false;
  showFirstRunHint = localStorage.getItem('ananta.instruction-first-run') !== 'true';

  profiles: any[] = [];
  overlays: any[] = [];
  profileExamples: ProfileExample[] = [];
  overlayPresets: OverlayPreset[] = [
    { id: 'task-test-first', title: 'Task: Test First', prompt: 'For this task, prioritize test coverage before implementation.', scope: 'task', attachment_kind: 'task' },
    { id: 'session-review', title: 'Session: Review Fokus', prompt: 'In this session, start with risks and edge cases before edits.', scope: 'session', attachment_kind: 'session' },
    { id: 'goal-migration', title: 'Goal: Migration Fokus', prompt: 'For this goal, keep migration compatibility and rollback paths visible.', scope: 'goal', attachment_kind: 'goal' },
  ];
  overlayScopes = ['task', 'goal', 'session', 'usage', 'one_shot', 'project'];
  overlayAttachmentKinds = ['task', 'goal', 'session', 'usage'];

  selectedProfilePresetId = '';
  selectedOverlayPresetId = '';
  editingProfileId = '';
  editingOverlayId = '';

  profileForm: any = this.emptyProfileForm();
  overlayForm: any = this.emptyOverlayForm();
  selectionForm: any = { task_id: '', goal_id: '', profile_id: '', overlay_id: '' };
  reuseForm: any = { overlay_id: '', session_id: '', usage_key: '' };
  effectiveForm: any = { task_id: '', goal_id: '', session_id: '', usage_key: '', profile_id: '', overlay_id: '' };

  lastSelectionResult: any = null;
  effectivePreview: any = null;
  effectiveError = '';

  ngOnInit(): void {
    this.refreshData();
  }

  dismissFirstRunHint(): void {
    this.showFirstRunHint = false;
    localStorage.setItem('ananta.instruction-first-run', 'true');
  }

  refreshData(): void {
    if (!this.hubUrl) return;
    this.busy = true;
    forkJoin({
      profiles: this.hubApi.listInstructionProfiles(this.hubUrl),
      overlays: this.hubApi.listInstructionOverlays(this.hubUrl),
      examples: this.hubApi.listInstructionProfileExamples(this.hubUrl),
    }).subscribe({
      next: ({ profiles, overlays, examples }) => {
        this.profiles = profiles || [];
        this.overlays = overlays || [];
        this.profileExamples = examples || [];
        this.ensureSelectionReferences();
        this.busy = false;
      },
      error: () => {
        this.busy = false;
        this.ns.error('Instruction-Layer-Daten konnten nicht geladen werden');
      },
    });
  }

  applyProfilePreset(id: string): void {
    const example = this.profileExamples.find(item => item.id === id);
    if (!example) return;
    this.profileForm.name = String(example.name || '').trim();
    this.profileForm.prompt_content = String(example.prompt_content || '').trim();
    const preferences = dictOrEmpty(example.profile_metadata?.preferences);
    this.profileForm.style = String(preferences.style || '');
    this.profileForm.language = String(preferences.language || '');
    this.profileForm.detail_level = String(preferences.detail_level || '');
    this.profileForm.working_mode = String(preferences.working_mode || '');
    this.profileForm.is_active = true;
  }

  saveProfile(): void {
    if (!this.hubUrl) return;
    const payload: any = {
      name: String(this.profileForm.name || '').trim(),
      prompt_content: String(this.profileForm.prompt_content || '').trim(),
      is_active: !!this.profileForm.is_active,
      is_default: !!this.profileForm.is_default,
      profile_metadata: this.profileMetadataFromForm(),
    };
    if (!payload.name || !payload.prompt_content) {
      this.ns.error('Name und Prompt sind erforderlich');
      return;
    }
    const request$ = this.editingProfileId
      ? this.hubApi.patchInstructionProfile(this.hubUrl, this.editingProfileId, payload)
      : this.hubApi.createInstructionProfile(this.hubUrl, payload);
    this.busy = true;
    request$.subscribe({
      next: () => {
        this.toast.success(this.editingProfileId ? 'Profil aktualisiert' : 'Profil angelegt');
        this.busy = false;
        this.resetProfileForm();
        this.refreshData();
      },
      error: (err) => {
        this.busy = false;
        this.ns.error(this.apiErrorMessage(err, 'Profil konnte nicht gespeichert werden'));
      },
    });
  }

  editProfile(profile: any): void {
    this.editingProfileId = String(profile?.id || '');
    const metadata = dictOrEmpty(profile?.profile_metadata);
    const preferences = dictOrEmpty(metadata.preferences);
    const compatibility = dictOrEmpty(metadata.compatibility);
    this.profileForm = {
      name: String(profile?.name || ''),
      prompt_content: String(profile?.prompt_content || ''),
      style: String(preferences.style || ''),
      language: String(preferences.language || ''),
      detail_level: String(preferences.detail_level || ''),
      working_mode: String(preferences.working_mode || ''),
      blocked_template_contexts: Array.isArray(compatibility.blocked_template_contexts)
        ? compatibility.blocked_template_contexts.join(',')
        : '',
      is_active: !!profile?.is_active,
      is_default: !!profile?.is_default,
    };
  }

  resetProfileForm(): void {
    this.editingProfileId = '';
    this.selectedProfilePresetId = '';
    this.profileForm = this.emptyProfileForm();
  }

  selectDefaultProfile(profileId: string): void {
    if (!this.hubUrl) return;
    this.hubApi.selectInstructionProfile(this.hubUrl, profileId).subscribe({
      next: () => this.refreshData(),
      error: (err) => this.ns.error(this.apiErrorMessage(err, 'Profil konnte nicht als Default gesetzt werden')),
    });
  }

  toggleProfileActive(profile: any): void {
    if (!this.hubUrl) return;
    this.hubApi.patchInstructionProfile(this.hubUrl, profile.id, { is_active: !profile.is_active }).subscribe({
      next: () => this.refreshData(),
      error: (err) => this.ns.error(this.apiErrorMessage(err, 'Profilstatus konnte nicht geaendert werden')),
    });
  }

  deleteProfile(profileId: string): void {
    if (!this.hubUrl) return;
    this.hubApi.deleteInstructionProfile(this.hubUrl, profileId).subscribe({
      next: () => this.refreshData(),
      error: (err) => this.ns.error(this.apiErrorMessage(err, 'Profil konnte nicht geloescht werden')),
    });
  }

  applyOverlayPreset(id: string): void {
    const preset = this.overlayPresets.find(item => item.id === id);
    if (!preset) return;
    this.overlayForm.name = preset.id;
    this.overlayForm.prompt_content = preset.prompt;
    this.overlayForm.scope = preset.scope;
    this.overlayForm.attachment_kind = preset.attachment_kind;
    this.overlayForm.is_active = true;
  }

  saveOverlay(): void {
    if (!this.hubUrl) return;
    const payload: any = {
      name: String(this.overlayForm.name || '').trim(),
      prompt_content: String(this.overlayForm.prompt_content || '').trim(),
      scope: String(this.overlayForm.scope || 'task'),
      attachment_kind: String(this.overlayForm.attachment_kind || '').trim() || null,
      attachment_id: String(this.overlayForm.attachment_id || '').trim() || null,
      is_active: !!this.overlayForm.is_active,
      overlay_metadata: this.overlayMetadataFromForm(),
    };
    if (!payload.name || !payload.prompt_content) {
      this.ns.error('Name und Prompt sind erforderlich');
      return;
    }
    const request$ = this.editingOverlayId
      ? this.hubApi.patchInstructionOverlay(this.hubUrl, this.editingOverlayId, payload)
      : this.hubApi.createInstructionOverlay(this.hubUrl, payload);
    this.busy = true;
    request$.subscribe({
      next: () => {
        this.toast.success(this.editingOverlayId ? 'Overlay aktualisiert' : 'Overlay angelegt');
        this.busy = false;
        this.resetOverlayForm();
        this.refreshData();
      },
      error: (err) => {
        this.busy = false;
        this.ns.error(this.apiErrorMessage(err, 'Overlay konnte nicht gespeichert werden'));
      },
    });
  }

  editOverlay(overlay: any): void {
    this.editingOverlayId = String(overlay?.id || '');
    const metadata = dictOrEmpty(overlay?.overlay_metadata);
    const preferences = dictOrEmpty(metadata.preferences);
    const lifecycle = dictOrEmpty(metadata.lifecycle);
    this.overlayForm = {
      name: String(overlay?.name || ''),
      prompt_content: String(overlay?.prompt_content || ''),
      scope: String(overlay?.scope || 'task'),
      attachment_kind: String(overlay?.attachment_kind || ''),
      attachment_id: String(overlay?.attachment_id || ''),
      working_mode: String(preferences.working_mode || ''),
      max_uses: lifecycle.max_uses || '',
      is_active: !!overlay?.is_active,
    };
  }

  resetOverlayForm(): void {
    this.editingOverlayId = '';
    this.selectedOverlayPresetId = '';
    this.overlayForm = this.emptyOverlayForm();
  }

  selectOverlay(overlayId: string): void {
    if (!this.hubUrl) return;
    this.hubApi.selectInstructionOverlay(this.hubUrl, overlayId, {}).subscribe({
      next: () => this.refreshData(),
      error: (err) => this.ns.error(this.apiErrorMessage(err, 'Overlay konnte nicht aktiviert werden')),
    });
  }

  detachOverlay(overlayId: string): void {
    if (!this.hubUrl) return;
    this.hubApi.detachInstructionOverlay(this.hubUrl, overlayId).subscribe({
      next: () => this.refreshData(),
      error: (err) => this.ns.error(this.apiErrorMessage(err, 'Overlay konnte nicht detached werden')),
    });
  }

  deleteOverlay(overlayId: string): void {
    if (!this.hubUrl) return;
    this.hubApi.deleteInstructionOverlay(this.hubUrl, overlayId).subscribe({
      next: () => this.refreshData(),
      error: (err) => this.ns.error(this.apiErrorMessage(err, 'Overlay konnte nicht geloescht werden')),
    });
  }

  applyTaskSelection(): void {
    if (!this.hubUrl || !this.selectionForm.task_id) return;
    this.hubApi.setTaskInstructionSelection(this.hubUrl, this.selectionForm.task_id, {
      profile_id: this.selectionForm.profile_id || null,
      overlay_id: this.selectionForm.overlay_id || null,
    }).subscribe({
      next: (result) => {
        this.lastSelectionResult = result;
        this.toast.success('Task-Auswahl gespeichert');
      },
      error: (err) => this.ns.error(this.apiErrorMessage(err, 'Task-Auswahl fehlgeschlagen')),
    });
  }

  applyGoalSelection(): void {
    if (!this.hubUrl || !this.selectionForm.goal_id) return;
    this.hubApi.setGoalInstructionSelection(this.hubUrl, this.selectionForm.goal_id, {
      profile_id: this.selectionForm.profile_id || null,
      overlay_id: this.selectionForm.overlay_id || null,
    }).subscribe({
      next: (result) => {
        this.lastSelectionResult = result;
        this.toast.success('Goal-Auswahl gespeichert');
      },
      error: (err) => this.ns.error(this.apiErrorMessage(err, 'Goal-Auswahl fehlgeschlagen')),
    });
  }

  attachOverlayToSession(): void {
    if (!this.hubUrl || !this.reuseForm.overlay_id || !this.reuseForm.session_id) return;
    this.hubApi.attachInstructionOverlay(this.hubUrl, this.reuseForm.overlay_id, {
      attachment_kind: 'session',
      attachment_id: this.reuseForm.session_id,
    }).subscribe({
      next: () => {
        this.toast.success('Overlay an Session gebunden');
        this.refreshData();
      },
      error: (err) => this.ns.error(this.apiErrorMessage(err, 'Session-Bindung fehlgeschlagen')),
    });
  }

  attachOverlayToUsage(): void {
    if (!this.hubUrl || !this.reuseForm.overlay_id || !this.reuseForm.usage_key) return;
    this.hubApi.attachInstructionOverlay(this.hubUrl, this.reuseForm.overlay_id, {
      attachment_kind: 'usage',
      attachment_id: this.reuseForm.usage_key,
    }).subscribe({
      next: () => {
        this.toast.success('Overlay an Usage gebunden');
        this.refreshData();
      },
      error: (err) => this.ns.error(this.apiErrorMessage(err, 'Usage-Bindung fehlgeschlagen')),
    });
  }

  previewEffective(): void {
    if (!this.hubUrl) return;
    this.effectiveError = '';
    this.hubApi.getInstructionLayersEffective(this.hubUrl, {
      task_id: this.effectiveForm.task_id || undefined,
      goal_id: this.effectiveForm.goal_id || undefined,
      session_id: this.effectiveForm.session_id || undefined,
      usage_key: this.effectiveForm.usage_key || undefined,
      profile_id: this.effectiveForm.profile_id || undefined,
      overlay_id: this.effectiveForm.overlay_id || undefined,
      base_prompt: 'instruction-layer-ui-preview',
    }).subscribe({
      next: (payload) => {
        this.effectivePreview = payload;
      },
      error: (err) => {
        this.effectivePreview = null;
        this.effectiveError = this.apiErrorMessage(err, 'Effective Preview konnte nicht geladen werden');
      },
    });
  }

  private emptyProfileForm(): any {
    return {
      name: '',
      prompt_content: '',
      style: '',
      language: '',
      detail_level: '',
      working_mode: '',
      blocked_template_contexts: '',
      is_active: true,
      is_default: false,
    };
  }

  private emptyOverlayForm(): any {
    return {
      name: '',
      prompt_content: '',
      scope: 'task',
      attachment_kind: '',
      attachment_id: '',
      working_mode: '',
      max_uses: '',
      is_active: true,
    };
  }

  private profileMetadataFromForm(): any {
    const preferences: any = {};
    if (this.profileForm.style) preferences.style = this.profileForm.style;
    if (this.profileForm.language) preferences.language = this.profileForm.language;
    if (this.profileForm.detail_level) preferences.detail_level = this.profileForm.detail_level;
    if (this.profileForm.working_mode) preferences.working_mode = this.profileForm.working_mode;
    const compatibility: any = {};
    const blockedContexts = csvToList(this.profileForm.blocked_template_contexts);
    if (blockedContexts.length) compatibility.blocked_template_contexts = blockedContexts;
    const metadata: any = {};
    if (Object.keys(preferences).length) metadata.preferences = preferences;
    if (Object.keys(compatibility).length) metadata.compatibility = compatibility;
    return metadata;
  }

  private overlayMetadataFromForm(): any {
    const metadata: any = {};
    const preferences: any = {};
    if (this.overlayForm.working_mode) preferences.working_mode = this.overlayForm.working_mode;
    if (Object.keys(preferences).length) metadata.preferences = preferences;
    const maxUses = Number(this.overlayForm.max_uses || 0);
    if (Number.isFinite(maxUses) && maxUses > 0) metadata.lifecycle = { max_uses: maxUses };
    return metadata;
  }

  private ensureSelectionReferences(): void {
    const profileIds = new Set(this.profiles.map(item => item.id));
    const overlayIds = new Set(this.overlays.map(item => item.id));
    if (this.selectionForm.profile_id && !profileIds.has(this.selectionForm.profile_id)) this.selectionForm.profile_id = '';
    if (this.selectionForm.overlay_id && !overlayIds.has(this.selectionForm.overlay_id)) this.selectionForm.overlay_id = '';
    if (this.reuseForm.overlay_id && !overlayIds.has(this.reuseForm.overlay_id)) this.reuseForm.overlay_id = '';
    if (this.effectiveForm.profile_id && !profileIds.has(this.effectiveForm.profile_id)) this.effectiveForm.profile_id = '';
    if (this.effectiveForm.overlay_id && !overlayIds.has(this.effectiveForm.overlay_id)) this.effectiveForm.overlay_id = '';
  }

  private apiErrorMessage(err: any, fallback: string): string {
    return String(err?.error?.message || err?.message || fallback);
  }
}

function csvToList(value: string): string[] {
  return String(value || '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean);
}

function dictOrEmpty(value: any): any {
  return value && typeof value === 'object' ? value : {};
}
