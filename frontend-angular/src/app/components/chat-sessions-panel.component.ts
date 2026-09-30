import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import {
  ChatSessionsService,
  ChatSession,
  ChatProfile,
  ChatSessionType,
  ChatFolder,
  ReorganizeProposal,
  OrganizationRevision,
  ContextOverview,
  CreateSessionPayload,
  PromptPreview,
  ChatSettingDefinition,
  ChatSettingValue,
} from '../services/chat-sessions.service';
import { ChatHistoryService } from '../services/chat-history.service';
import { ChatOrganizationDialogsComponent } from './chat-organization-dialogs.component';
import { ChatProfileEditorComponent } from './chat-profile-editor.component';
import { ChatSettingControlsComponent } from './chat-setting-controls.component';
import { ChatProcessBindingEditorComponent } from './chat-process-binding-editor.component';
import { CHAT_SESSIONS_PANEL_STYLES } from './chat-sessions-panel.styles';
import {
  FolderNode,
  TreeItem,
  buildChatFolderTree,
  flattenChatFolderTree,
  isChatFolderDescendant,
} from './chat-sessions-folder-tree';
import { PugPresetName, pugPresetSettingEntries, pugPresetDescription, resolvePugPreset } from './chat-sessions-pug-presets';

@Component({
  selector: 'app-chat-sessions-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, ChatOrganizationDialogsComponent, ChatProfileEditorComponent, ChatSettingControlsComponent, ChatProcessBindingEditorComponent],
  template: `
    <div class="sessions-panel">

      <!-- ── Top bar: AI reorganize ── -->
      <div class="top-bar">
        <select [(ngModel)]="reorganizeInputPolicy" title="Daten für KI-Reorganisation">
          <option value="metadata_only">Nur Metadaten</option>
          <option value="metadata_plus_preview">Metadaten + Vorschau</option>
        </select>
        <button class="reorg-btn" (click)="startReorganize()" [disabled]="reorganizing">
          {{ reorganizing ? '⟳ Analysiere...' : '🤖 Neu-Strukturieren' }}
        </button>
        <button class="new-folder-btn" (click)="createRootFolder()" title="Neuen Ordner anlegen">📁＋</button>
        <button class="new-folder-btn" (click)="showProfiles = !showProfiles" title="Chat-Profile verwalten">🎛 Profile</button>
        <button class="new-folder-btn" (click)="openHistory()" title="Organisationsverlauf">↶ Verlauf</button>
        @if (loading) {
          <span class="loading-indicator">⟳</span>
        }
      </div>

      @if (showProfiles) {
        <app-chat-profile-editor />
      }

      <!-- ── Session + folder list ── -->
      <div class="list">
        @if (draggingSessionId || draggingFolderId) {
          <div class="root-drop-zone"
               [class.drag-over]="dragOverFolderId === '__root__'"
               (dragover)="onDragOver($event, '__root__')"
               (dragleave)="onDragLeave()"
               (drop)="onDropRoot($event)">
            ⤒ Hierher ziehen: aus Ordner entfernen
          </div>
        }
        @for (item of getFlatTree(); track item.trackId) {

          <!-- Folder header -->
          @if (item.kind === 'folder-header' && item.folder; as node) {
            <div class="folder-row"
                 draggable="true"
                 [class.drag-over]="dragOverFolderId === node.folder.id"
                 [style.padding-left.px]="8 + item.depth * 14"
                 (click)="toggleFolder(node.folder.id)"
                 (dragover)="onDragOver($event, node.folder.id)"
                 (dragleave)="onDragLeave()"
                 (drop)="onDrop($event, node.folder.id)">
              <span (dragstart)="onFolderDragStart($event, node.folder.id)" (dragend)="onDragEnd()">
              <span class="folder-chevron">{{ isFolderCollapsed(node.folder.id) ? '▶' : '▼' }}</span>
              <span class="folder-icon">{{ node.folder.icon || '📁' }}</span>
              <span class="folder-name">{{ node.folder.name }}</span>
              </span>
              <button class="icon-btn" title="Unterordner anlegen"
                      (click)="$event.stopPropagation(); createSubfolder(node.folder.id)">+</button>
              <button class="icon-btn" title="Umbenennen"
                      (click)="$event.stopPropagation(); renameFolder(node.folder)">✎</button>
              <button class="icon-btn del" title="Ordner löschen"
                      (click)="$event.stopPropagation(); deleteFolderConfirm(node.folder)">✕</button>
            </div>
          }

          <!-- Session row -->
          @if (item.kind === 'session') {
            @if (item.session; as s) {
              <div class="session-row"
                   [class.active]="s.id === activeSessionId"
                   [style.padding-left.px]="8 + item.depth * 14"
                   draggable="true"
                   (dragstart)="onDragStart(s)"
                   (dragend)="onDragEnd()">

                @if (editingId === s.id) {
                  <input class="name-input" [(ngModel)]="editName"
                         (keydown.enter)="saveEdit(s)" (keydown.escape)="cancelEdit()" />
                  <button class="icon-btn ok" (click)="saveEdit(s)" title="Speichern">✓</button>
                  <button class="icon-btn"    (click)="cancelEdit()"  title="Abbrechen">✕</button>
                } @else {
                  <!-- Icon with hover tooltip -->
                  <div class="icon-wrap"
                       (mouseenter)="tooltipId = s.id"
                       (mouseleave)="tooltipId = ''">
                    <span class="sess-icon">{{ s.icon || '💬' }}</span>
                    @if (tooltipId === s.id) {
                      <div class="session-tooltip">
                        <div class="tt-row tt-name">{{ s.name }}</div>
                        <div class="tt-row tt-type">{{ s.session_type || s.group || 'Allgemein' }}</div>
                        @if (s.type_description) {
                          <div class="tt-desc">{{ s.type_description }}</div>
                        }
                        @if (msgCount(s) > 0) {
                          <div class="tt-row">{{ msgCount(s) }} Nachrichten</div>
                        }
                        @if (lastPreview(s)) {
                          <div class="tt-preview">"{{ lastPreview(s) }}"</div>
                        }
                        @if (s.updated_at) {
                          <div class="tt-row tt-muted">{{ formatDate(s.updated_at) }}</div>
                        }
                      </div>
                    }
                  </div>

                  <button class="session-btn" (click)="activate(s)">
                    <span class="sess-name">{{ s.name }}</span>
                    @if (s.id === activeSessionId) {
                      <span class="active-dot">●</span>
                    }
                    @if (deltaCount(s) > 0) {
                      <span class="delta-badge" title="{{ deltaCount(s) }} Einstellung(en) überschrieben">{{ deltaCount(s) }}</span>
                    }
                  </button>
                  <button class="icon-btn cfg" [class.cfg-open]="expandedId === s.id"
                          (click)="toggleSettings(s)" title="Einstellungen">⚙</button>
                  <button class="icon-btn" (click)="startEdit(s)" title="Umbenennen">✎</button>
                  <button class="icon-btn del"
                          (click)="confirmDelete(s)"
                          [disabled]="sessions.length <= 1"
                          title="Session löschen">✕</button>
                }
              </div>

              <!-- ── Per-session settings panel ── -->
              @if (expandedId === s.id && editingId !== s.id) {
                <div class="cfg-panel" [style.padding-left.px]="12 + item.depth * 14">
                  <div class="cfg-hint">
                    Nur abweichende Werte werden gespeichert.
                    <span class="cfg-hint-badge">{{ deltaCount(s) }} Override(s)</span>
                  </div>
                  <div class="editor-tabs"><button (click)="setSessionTab(s,'base')" [class.active]="sessionTab(s)==='base'">Session</button><button (click)="setSessionTab(s,'process')" [class.active]="sessionTab(s)==='process'">Prozess</button><button (click)="setSessionTab(s,'settings')" [class.active]="sessionTab(s)==='settings'">Einstellungen</button></div>

                  <div class="cfg-row">
                    <label class="cfg-label">Chat-Profil</label>
                    <select [ngModel]="s.profile_id || 'general'" (ngModelChange)="changeProfile(s, $event)">
                      @for (profile of profiles; track profile.id) {
                        <option [value]="profile.id">{{ profile.icon || '🎯' }} {{ profile.name }}</option>
                      }
                    </select>
                  </div>
                  <div class="cfg-row">
                    <label class="cfg-label">Session-Typ</label>
                    <select [ngModel]="s.session_type || ''" (ngModelChange)="changeType(s, $event)">
                      <option value="">(nicht klassifiziert)</option>
                      @for (type of types; track type.id) { <option [value]="type.id">{{ type.icon }} {{ type.name }}</option> }
                    </select>
                  </div>
                  @if (typeFor(s.session_type)?.subtypes?.length) {
                    <div class="cfg-row">
                      <label class="cfg-label">Subtyp</label>
                      <select [ngModel]="s.session_subtype || ''" (ngModelChange)="changeSubtype(s, $event)">
                        <option value="">(kein Subtyp)</option>
                        @for (subtype of typeFor(s.session_type)!.subtypes; track subtype) { <option [value]="subtype">{{ subtype }}</option> }
                      </select>
                    </div>
                  }

                  @if(sessionTab(s)==='settings'){<app-chat-setting-controls [settings]="settingSchema" scope="session"
                    [delta]="s.settings_delta || {}" [effective]="s.settings || {}" overrideLabel="Session-Override"
                    (changed)="patchSetting(s,$event.key,$event.value)" (reset)="resetSetting(s,$event)" (resetAll)="resetAllSettings(s)" />}
                  @if(sessionTab(s)==='process'){<app-chat-process-binding-editor [sessionId]="s.id" [processRef]="s.process_ref||null" [profileProcessRef]="profileProcessRef(s)" />}

                  <!-- PUG Predictive-Guide Presets — only for ananta-visual session -->
                  @if (s.id === 'ananta-visual') {
                    <div class="pug-section">
                      <div class="pug-title">Predictive Guide (PUG)</div>
                      <div class="pug-preset-bar">
                        <button class="pug-btn" [class.active]="pugPreset(s)==='quiet'"    (click)="applyPugPreset(s,'quiet')">Quiet</button>
                        <button class="pug-btn" [class.active]="pugPreset(s)==='balanced'" (click)="applyPugPreset(s,'balanced')">Balanced</button>
                        <button class="pug-btn" [class.active]="pugPreset(s)==='eager'"    (click)="applyPugPreset(s,'eager')">Eager</button>
                        @if (pugPreset(s)==='custom') {
                          <span class="pug-custom">Custom</span>
                        }
                      </div>
                      <div class="pug-desc">{{ pugDescription(s) }}</div>
                      <div class="cfg-checkboxes" style="margin-top:4px">
                        <label class="cfg-check">
                          <input type="checkbox"
                                 [ngModel]="getBool(s,'predictive_guide_enabled')"
                                 (ngModelChange)="patchSetting(s,'predictive_guide_enabled',$event)" />
                          PUG aktiv
                        </label>
                      </div>
                    </div>
                  }

                  <!-- Group -->
                  <div class="cfg-row">
                    <label class="cfg-label">Gruppe</label>
                    <input type="text" [ngModel]="s.group || ''"
                           (ngModelChange)="patchGroup(s, $event)"
                           placeholder="(keine Gruppe)" />
                  </div>

                  <!-- System-Prompt -->
                  <label class="cfg-label-block">
                    System-Prompt
                    <textarea rows="4"
                              [ngModel]="s.system_prompt"
                              (ngModelChange)="patchPromptDebounced(s, $event)"
                              placeholder="Leer = Standard-Prompt des Systems"></textarea>
                  </label>

                  <!-- ── Chat-Verlauf & Kontext ── -->
                  <div class="ctx-section">
                    <div class="ctx-title">Chat-Verlauf &amp; Kontext</div>

                    <!-- Kontext-Überblick button + table -->
                    <div class="ctx-btn-row">
                      <button class="ctx-overview-btn" (click)="toggleContextOverview(s)">
                        Kontext-Überblick anzeigen {{ contextOverviewExpanded[s.id] ? '▲' : '▼' }}
                      </button>
                      <button class="ctx-overview-btn" (click)="openPromptPreview(s)">Prompt-Vorschau ⧉</button>
                    </div>

                    @if (contextOverviewExpanded[s.id]) {
                      @if (contextOverviews[s.id]; as ov) {
                        <table class="ctx-table">
                          <tr>
                            <td class="ctx-td-label">System-Prompt</td>
                            <td class="ctx-td-val">{{ ov.system_prompt.chars }} Zeichen {{ ov.system_prompt.enabled ? '(aktiv)' : '(inaktiv)' }}</td>
                          </tr>
                          <tr>
                            <td class="ctx-td-label">Verlauf</td>
                            <td class="ctx-td-val">{{ ov.history.enabled ? 'aktiv' : 'inaktiv' }}, {{ ov.history.max_turns }} Turns, max. {{ ov.history.max_chars }} Zeichen</td>
                          </tr>
                          <tr>
                            <td class="ctx-td-label">Zusammenfassung</td>
                            <td class="ctx-td-val">{{ ov.summary.enabled ? 'aktiv' : 'inaktiv' }}, max. {{ ov.summary.max_chars }} Zeichen</td>
                          </tr>
                          <tr>
                            <td class="ctx-td-label">RAG</td>
                            <td class="ctx-td-val">{{ ov.rag.profile }}, Top-K: {{ ov.rag.top_k }}, max. {{ ov.rag.max_chars }} Zeichen</td>
                          </tr>
                        </table>
                      } @else {
                        <div class="ctx-loading">Lade Kontext-Überblick...</div>
                      }
                    }

                    <!-- Verlauf löschen -->
                    <button class="ctx-clear-btn" (click)="clearHistory(s)">Verlauf löschen</button>
                  </div>

                  @if (deltaCount(s) > 0) {
                    <button class="reset-all-btn" (click)="resetAllSettings(s)">
                      ↩ Alle Overrides zurücksetzen ({{ deltaCount(s) }})
                    </button>
                  }
                  <button class="close-cfg-btn" (click)="expandedId = ''">Schließen ▲</button>
                </div>
              }
            }
          }

        }
      </div>

      <!-- ── New session form ── -->
      <div class="bottom-bar">
        <button class="new-btn" (click)="showNew = !showNew">
          {{ showNew ? '▲ Abbrechen' : '＋ Neuer Chat' }}
        </button>
      </div>

      @if (showNew) {
        <div class="new-form">
          <label class="cfg-label-block">Chat-Profil
            <select [(ngModel)]="newProfileId" (ngModelChange)="selectProfile($event)">
              @for (profile of profiles; track profile.id) {
                <option [value]="profile.id">{{ profile.icon || '🎯' }} {{ profile.name }}</option>
              }
            </select>
            <span class="muted">Das Profil bestimmt Prompt, Backend und Kontextregeln. Der Chat behält seinen eigenen Verlauf.</span>
          </label>
          <label class="cfg-label-block">Session-Typ
            <select [(ngModel)]="newSessionType" (ngModelChange)="newSessionSubtype = ''">
              <option value="">(nicht klassifiziert)</option>
              @for (type of types; track type.id) { <option [value]="type.id">{{ type.icon }} {{ type.name }}</option> }
            </select>
          </label>
          @if (selectedType()?.subtypes?.length) {
            <label class="cfg-label-block">Subtyp
              <select [(ngModel)]="newSessionSubtype">
                <option value="">(kein Subtyp)</option>
                @for (subtype of selectedType()!.subtypes; track subtype) { <option [value]="subtype">{{ subtype }}</option> }
              </select>
            </label>
          }
          <button class="type-btn" (click)="showCustomType = !showCustomType">＋ Eigenen Session-Typ anlegen</button>
          @if (showCustomType) {
            <div class="custom-type-form">
              <div class="muted">Typ/Subtyp klassifiziert Inhalt und Zweck; das Profil bestimmt die Arbeitsweise.</div>
              @for (type of types; track type.id) {
                <div class="profile-row">
                  <span>{{ type.icon }} {{ type.name }} <span class="muted">{{ type.subtypes.join(', ') }}</span></span>
                  @if (!type.builtin) {
                    <button class="icon-btn" (click)="editCustomType(type)">✎</button>
                    <button class="icon-btn del" (click)="deleteCustomType(type)">✕</button>
                  }
                </div>
              }
              <input [(ngModel)]="customTypeIcon" placeholder="🎯" maxlength="4" />
              <input [(ngModel)]="customTypeName" placeholder="Name des Typs" />
              <input [(ngModel)]="customTypeSubtypes" placeholder="Subtypen, kommagetrennt" />
              <button (click)="createCustomType()" [disabled]="!customTypeName.trim()">Typ anlegen</button>
            </div>
          }

          <div class="new-form-row">
            <input [(ngModel)]="newIcon" placeholder="🤖" maxlength="4" class="icon-field" />
            <input [(ngModel)]="newName" placeholder="Name *" class="name-field"
                   (keydown.enter)="createNew()" />
          </div>
          <div class="new-form-row">
            <input [(ngModel)]="newGroup" placeholder="Gruppe (optional)" class="group-field" />
          </div>

          <!-- Folder picker -->
          <label class="cfg-label-block">Ordner
            <select [(ngModel)]="newFolderId">
              <option value="">(kein Ordner)</option>
              @for (f of folders; track f.id) {
                <option [value]="f.id">{{ f.icon || '📁' }} {{ f.name }}</option>
              }
            </select>
          </label>

          <label class="cfg-label-block">
            System-Prompt (optional)
            <textarea rows="3" [(ngModel)]="newPrompt"
                      placeholder="z.B. Antworte nur mit Mermaid-Diagrammen."></textarea>
          </label>
          <button class="create-btn" (click)="createNew()" [disabled]="!newName.trim()">
            Session anlegen
          </button>
        </div>
      }

      @if (error) {
        <div class="err">{{ error }}</div>
      }

    </div><!-- /.sessions-panel -->

    <app-chat-organization-dialogs
      [proposal]="reorganizeProposal"
      [sessions]="sessions"
      [folders]="folders"
      [history]="organizationHistory"
      [historyVisible]="showOrganizationHistory"
      (apply)="acceptReorganize()"
      (cancel)="cancelReorganize()"
      (proposalChanged)="reorganizeProposal = $event"
      (revert)="revertRevision($event)"
      (closeHistory)="showOrganizationHistory = false" />

    <!-- ── Prompt preview modal ── -->
    @if (promptPreviewSession) {
      <div class="modal-backdrop" (click)="closePromptPreview()">
        <div class="modal preview-modal" (click)="$event.stopPropagation()">
          <div class="modal-title">⧉ Prompt-Vorschau — {{ promptPreviewSession }}</div>
          @if (promptPreview; as pv) {
            <div class="preview-sections">
              @for (sec of pv.sections; track sec.name) {
                <div class="preview-sec-row">
                  <span class="preview-sec-name">{{ sectionLabel(sec.name) }}</span>
                  <span class="preview-badge" [class.on]="sec.enabled">{{ sec.enabled ? 'aktiv' : 'inaktiv' }}</span>
                  <span class="preview-chars">{{ sec.chars }} Zeichen</span>
                  @if (sec.truncated) {
                    <span class="preview-trunc">⚠ gekürzt</span>
                  }
                </div>
              }
            </div>
            <div class="preview-total">Gesamt: {{ pv.total_chars }} Zeichen</div>
            <pre class="preview-pre">{{ pv.assembled_prompt }}</pre>
            <div class="modal-actions">
              <button class="btn-cancel" (click)="copyPromptPreview()">
                {{ previewCopied ? '✓ Kopiert' : 'In Zwischenablage kopieren' }}
              </button>
              <button class="btn-accept" (click)="closePromptPreview()">Schließen</button>
            </div>
          } @else if (promptPreviewError) {
            <div class="err">{{ promptPreviewError }}</div>
            <div class="modal-actions">
              <button class="btn-cancel" (click)="closePromptPreview()">Schließen</button>
            </div>
          } @else {
            <div class="ctx-loading">Lade Prompt-Vorschau...</div>
          }
        </div>
      </div>
    }
  `,
  styles: [CHAT_SESSIONS_PANEL_STYLES],
})
export class ChatSessionsPanelComponent implements OnInit, OnDestroy {
  readonly svc = inject(ChatSessionsService);
  private chatHistorySvc = inject(ChatHistoryService);

  // Locally mirrored state (subscribed in ngOnInit)
  sessions: ChatSession[] = [];
  folders: ChatFolder[] = [];
  profiles: ChatProfile[] = [];
  activeSessionId = '';
  error = '';
  loading = false;

  // UI state
  editingId = '';
  editName = '';
  expandedId = '';
  showNew = false;
  showProfiles = false;
  tooltipId = '';
  draggingSessionId = '';
  draggingFolderId = '';
  dragOverFolderId = '';
  collapsedFolders = new Set<string>();

  // AI Reorganize
  reorganizing = false;
  reorganizeProposal: ReorganizeProposal | null = null;
  reorganizeInputPolicy: 'metadata_only' | 'metadata_plus_preview' = 'metadata_only';
  organizationHistory: OrganizationRevision[] = [];
  showOrganizationHistory = false;

  // Context overview per session
  contextOverviews: Record<string, ContextOverview> = {};
  contextOverviewExpanded: Record<string, boolean> = {};

  // Prompt preview modal
  promptPreview: PromptPreview | null = null;
  promptPreviewSession = '';
  promptPreviewError = '';
  previewCopied = false;

  // Session types (from API)
  types: ChatSessionType[] = [];
  settingSchema: ChatSettingDefinition[] = [];
  sessionTabs:Record<string,'base'|'process'|'settings'>={};

  // New session form
  newName = '';
  newIcon = '💬';
  newGroup = '';
  newPrompt = '';
  newFolderId = '';
  newProfileId = 'general';
  newSessionType = '';
  newSessionSubtype = '';

  // Custom type form
  showCustomType = false;
  customTypeIcon = '🎯';
  customTypeName = '';
  customTypeSubtypes = '';

  private subs: Subscription[] = [];
  private promptDebounce: ReturnType<typeof setTimeout> | null = null;
  private groupDebounce: ReturnType<typeof setTimeout> | null = null;

  ngOnInit(): void {
    this.svc.load();
    this.subs.push(
      this.svc.sessions$.subscribe(s => { this.sessions = s; }),
      this.svc.folders$.subscribe(f => { this.folders = f; }),
      this.svc.profiles$.subscribe(p => { this.profiles = p; }),
      this.svc.types$.subscribe(t => { this.types = t; }),
      this.svc.settingSchema$.subscribe(schema => { this.settingSchema = schema.settings; }),
      this.svc.activeSessionId$.subscribe(id => { this.activeSessionId = id; }),
      this.svc.error$.subscribe(e => { this.error = e; }),
      this.svc.loading$.subscribe(l => { this.loading = l; }),
    );
  }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }

  // ── Folder tree ──────────────────────────────────────────────────────────

  buildFolderTree(folders: ChatFolder[], sessions: ChatSession[]): FolderNode[] {
    return buildChatFolderTree(folders, sessions);
  }

  getFlatTree(): TreeItem[] {
    return flattenChatFolderTree(this.folders, this.sessions, this.collapsedFolders);
  }

  toggleFolder(id: string): void {
    if (this.collapsedFolders.has(id)) {
      this.collapsedFolders.delete(id);
    } else {
      this.collapsedFolders.add(id);
    }
    // Trigger change detection by creating a new Set reference
    this.collapsedFolders = new Set(this.collapsedFolders);
  }

  isFolderCollapsed(id: string): boolean {
    return this.collapsedFolders.has(id);
  }

  // ── Drag & drop ──────────────────────────────────────────────────────────

  onDragStart(s: ChatSession): void {
    this.draggingSessionId = s.id;
    this.draggingFolderId = '';
  }

  onFolderDragStart(event: DragEvent, folderId: string): void {
    event.stopPropagation();
    this.draggingFolderId = folderId;
    this.draggingSessionId = '';
  }

  onDragEnd(): void {
    this.draggingSessionId = '';
    this.draggingFolderId = '';
    this.dragOverFolderId = '';
  }

  onDragOver(event: DragEvent, folderId: string): void {
    event.preventDefault();
    this.dragOverFolderId = folderId;
  }

  onDragLeave(): void {
    this.dragOverFolderId = '';
  }

  onDrop(event: DragEvent, folderId: string): void {
    event.preventDefault();
    if (this.draggingSessionId) {
      this.svc.update(this.draggingSessionId, { folder_id: folderId });
    } else if (this.draggingFolderId && this.draggingFolderId !== folderId && !this.isFolderDescendant(folderId, this.draggingFolderId)) {
      this.svc.updateFolder(this.draggingFolderId, { parent_id: folderId }).subscribe({
        error: () => this.svc.loadFolders(),
      });
    }
    this.draggingSessionId = '';
    this.draggingFolderId = '';
    this.dragOverFolderId = '';
  }

  onDropRoot(event: DragEvent): void {
    event.preventDefault();
    if (this.draggingSessionId) this.svc.update(this.draggingSessionId, { folder_id: '' });
    if (this.draggingFolderId) this.svc.updateFolder(this.draggingFolderId, { parent_id: '' }).subscribe();
    this.draggingSessionId = '';
    this.draggingFolderId = '';
    this.dragOverFolderId = '';
  }

  isFolderDescendant(candidateId: string, ancestorId: string): boolean {
    return isChatFolderDescendant(this.folders, candidateId, ancestorId);
  }

  // ── Folder actions ───────────────────────────────────────────────────────

  createRootFolder(): void {
    const name = prompt('Ordner-Name:');
    if (!name?.trim()) return;
    this.svc.createFolder(name.trim(), '📁').subscribe();
  }

  createSubfolder(parentId: string): void {
    const name = prompt('Ordner-Name:');
    if (!name?.trim()) return;
    this.svc.createFolder(name.trim(), '📁', parentId).subscribe();
  }

  renameFolder(f: ChatFolder): void {
    const name = prompt('Neuer Name:', f.name);
    if (!name?.trim() || name.trim() === f.name) return;
    this.svc.updateFolder(f.id, { name: name.trim() }).subscribe();
  }

  deleteFolderConfirm(f: ChatFolder): void {
    if (!confirm(`Ordner "${f.name}" wirklich löschen?`)) return;
    this.svc.deleteFolder(f.id).subscribe();
  }

  // ── AI Reorganize ────────────────────────────────────────────────────────

  startReorganize(): void {
    this.reorganizing = true;
    this.svc.aiReorganize(this.reorganizeInputPolicy).subscribe({
      next: proposal => {
        this.reorganizing = false;
        this.reorganizeProposal = proposal;
      },
      error: err => {
        this.reorganizing = false;
        this.error = String((err as Error)?.message || 'Fehler bei der KI-Reorganisierung');
      },
    });
  }

  acceptReorganize(): void {
    if (!this.reorganizeProposal) return;
    const proposal = this.reorganizeProposal;
    this.svc.updateProposal(proposal.id, { operations: proposal.operations }).subscribe({
      next: () => this.svc.validateProposal(proposal.id).subscribe({
        next: validated => {
          this.reorganizeProposal = { ...proposal, ...validated };
          if (validated.status !== 'ready') return;
          this.svc.applyProposal(proposal.id).subscribe({
            next: () => {
              this.reorganizeProposal = null;
              this.svc.loadFolders();
            },
            error: err => { this.error = String(err?.message || 'Atomare Anwendung fehlgeschlagen'); },
          });
        },
        error: err => { this.error = String(err?.message || 'Validierung fehlgeschlagen'); },
      }),
      error: err => { this.error = String(err?.message || 'Vorschlag konnte nicht gespeichert werden'); },
    });
  }

  cancelReorganize(): void {
    if (this.reorganizeProposal?.id && this.reorganizeProposal.status !== 'applied') {
      this.svc.discardProposal(this.reorganizeProposal.id).subscribe();
    }
    this.reorganizeProposal = null;
  }

  openHistory(): void {
    this.svc.loadOrganizationHistory().subscribe({
      next: revisions => { this.organizationHistory = revisions; this.showOrganizationHistory = true; },
      error: err => { this.error = String(err?.message || 'Verlauf konnte nicht geladen werden'); },
    });
  }

  revertRevision(revision: OrganizationRevision): void {
    const inversePreview = [...revision.applied_operations].reverse()
      .map(operation => `• ${operation.type}: ${String(operation.after ?? '–')} → ${String(operation.before ?? '–')}`)
      .join('\n');
    if (!confirm(`Revision „${revision.summary}“ rückgängig machen?\n\nVorschau:\n${inversePreview || 'Gespeicherten Zustand wiederherstellen'}`)) return;
    this.svc.revertRevision(revision.id).subscribe({
      next: () => { this.svc.loadFolders(); this.openHistory(); },
      error: err => { this.error = String(err?.message || 'Revision hat Konflikte und kann nicht rückgängig gemacht werden'); },
    });
  }

  // ── Context overview ─────────────────────────────────────────────────────

  loadContextOverview(s: ChatSession): void {
    this.contextOverviewExpanded = { ...this.contextOverviewExpanded, [s.id]: true };
    if (this.contextOverviews[s.id]) return;
    this.svc.getContextOverview(s.id).subscribe({
      next: overview => {
        this.contextOverviews = { ...this.contextOverviews, [s.id]: overview };
      },
      error: () => {
        // Leave the loading state; user can try again
      },
    });
  }

  toggleContextOverview(s: ChatSession): void {
    if (this.contextOverviewExpanded[s.id]) {
      this.contextOverviewExpanded = { ...this.contextOverviewExpanded, [s.id]: false };
    } else {
      this.loadContextOverview(s);
    }
  }

  // ── Prompt preview ───────────────────────────────────────────────────────

  openPromptPreview(s: ChatSession): void {
    this.promptPreview = null;
    this.promptPreviewError = '';
    this.previewCopied = false;
    this.promptPreviewSession = s.name;
    const history = this.chatHistorySvc.getVisibleMessages(s.id)
      .map(m => ({ sender: m.isAI ? 'ai' : 'user', text: m.text }));
    this.svc.getPromptPreview(s.id, '(Beispiel: nächste Nachricht)', history).subscribe({
      next: pv => { this.promptPreview = pv; },
      error: err => {
        this.promptPreviewError = String((err as Error)?.message || 'Fehler beim Laden der Prompt-Vorschau');
      },
    });
  }

  closePromptPreview(): void {
    this.promptPreview = null;
    this.promptPreviewSession = '';
    this.promptPreviewError = '';
    this.previewCopied = false;
  }

  copyPromptPreview(): void {
    const text = this.promptPreview?.assembled_prompt ?? '';
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      this.previewCopied = true;
      setTimeout(() => { this.previewCopied = false; }, 2000);
    }).catch(() => { /* clipboard unavailable — ignore */ });
  }

  sectionLabel(name: string): string {
    const map: Record<string, string> = {
      system_prompt: 'System-Prompt',
      summary: 'Zusammenfassung',
      history: 'Verlauf',
      rag: 'Kontext (RAG)',
      user_message: 'Neue Nachricht',
    };
    return map[name] ?? name;
  }

  // ── Session CRUD ─────────────────────────────────────────────────────────

  selectProfile(profileId: string): void {
    const profile = this.profiles.find(item => item.id === profileId);
    if (profile) this.newIcon = profile.icon || '💬';
  }

  changeProfile(session: ChatSession, profileId: string): void {
    this.svc.update(session.id, { profile_id: profileId });
  }

  changeType(session: ChatSession, typeId: string): void {
    this.svc.update(session.id, { session_type: typeId, session_subtype: '' });
  }

  changeSubtype(session: ChatSession, subtype: string): void {
    this.svc.update(session.id, { session_subtype: subtype });
  }

  typeFor(sessionType: string): ChatSessionType | undefined {
    return this.types.find(t => t.id === sessionType);
  }
  profileProcessRef(session:ChatSession){return this.profiles.find(profile=>profile.id===session.profile_id)?.process_ref||null;}
  sessionTab(session:ChatSession){return this.sessionTabs[session.id]||'base';}
  setSessionTab(session:ChatSession,tab:'base'|'process'|'settings'){this.sessionTabs[session.id]=tab;}

  selectedType(): ChatSessionType | undefined {
    return this.types.find(t => t.id === this.newSessionType);
  }

  createCustomType(): void {
    const name = this.customTypeName.trim();
    if (!name) return;
    const subtypes = this.customTypeSubtypes.split(',').map(s => s.trim()).filter(Boolean);
    this.svc.createType({
      name,
      icon: this.customTypeIcon || '🎯',
      subtypes,
    });
    this.customTypeName = '';
    this.customTypeIcon = '🎯';
    this.customTypeSubtypes = '';
    this.showCustomType = false;
  }

  editCustomType(type: ChatSessionType): void {
    const name = prompt('Name des Typs:', type.name)?.trim();
    if (!name) return;
    const subtypes = prompt('Subtypen, kommagetrennt:', type.subtypes.join(', '));
    if (subtypes == null) return;
    this.svc.updateType(type.id, {
      name,
      subtypes: subtypes.split(',').map(value => value.trim()).filter(Boolean),
    });
  }

  deleteCustomType(type: ChatSessionType): void {
    if (!confirm(`Typ „${type.name}“ löschen? Verwendete Typen werden serverseitig geschützt.`)) return;
    this.svc.deleteType(type.id);
  }

  activate(s: ChatSession): void {
    this.svc.activate(s.id);
  }

  toggleSettings(s: ChatSession): void {
    this.expandedId = this.expandedId === s.id ? '' : s.id;
    this.editingId = '';
  }

  startEdit(s: ChatSession): void {
    this.editingId = s.id;
    this.editName = s.name;
    this.expandedId = '';
  }

  saveEdit(s: ChatSession): void {
    const name = this.editName.trim();
    if (name && name !== s.name) this.svc.update(s.id, { name });
    this.editingId = '';
  }

  cancelEdit(): void { this.editingId = ''; }

  createNew(): void {
    const name = this.newName.trim();
    if (!name) return;
    const payload: CreateSessionPayload = {
      name,
      icon: this.newIcon || '💬',
      group: this.newGroup.trim(),
      folder_id: this.newFolderId,
      system_prompt: this.newPrompt,
      profile_id: this.newProfileId,
      session_type: this.newSessionType,
      session_subtype: this.newSessionSubtype,
    };
    this.svc.create(payload);
    this.resetNewForm();
  }

  private resetNewForm(): void {
    this.newName = '';
    this.newIcon = '💬';
    this.newGroup = '';
    this.newPrompt = '';
    this.newFolderId = '';
    this.newProfileId = 'general';
    this.newSessionType = '';
    this.newSessionSubtype = '';
    this.showNew = false;
  }

  confirmDelete(s: ChatSession): void {
    if (confirm(`Session "${s.name}" wirklich löschen?`)) {
      this.svc.remove(s.id);
    }
  }

  clearHistory(s: ChatSession): void {
    if (!confirm(`Chat-Verlauf für "${s.name}" wirklich löschen?`)) return;
    this.chatHistorySvc.clearSession(s.id);
  }

  // ── Tooltip data from local history ──────────────────────────────────────

  msgCount(s: ChatSession): number {
    return this.chatHistorySvc.getMessages(s.id).length;
  }

  lastPreview(s: ChatSession): string {
    const msgs = this.chatHistorySvc.getMessages(s.id);
    const last = msgs[msgs.length - 1];
    return last ? last.text.slice(0, 80) : '';
  }

  // ── Settings helpers ─────────────────────────────────────────────────────

  deltaCount(s: ChatSession): number {
    return Object.keys(s.settings_delta || {}).length;
  }

  isOverride(s: ChatSession, key: string): boolean {
    return key in (s.settings_delta || {});
  }

  getStr(s: ChatSession, key: string, fallback: string): string {
    const v = s.settings?.[key];
    return v === false || v === null || v === undefined ? fallback : String(v);
  }

  getNum(s: ChatSession, key: string, fallback: number): number {
    const v = s.settings?.[key];
    return typeof v === 'number' ? v : fallback;
  }

  getBool(s: ChatSession, key: string): boolean {
    return !!s.settings?.[key];
  }

  patchSetting(s: ChatSession, key: string, value: ChatSettingValue): void {
    this.svc.update(s.id, { settings: { [key]: value } });
  }

  resetSetting(s: ChatSession, key: string): void {
    if (!this.isOverride(s, key)) return;
    this.svc.update(s.id, { settings: { [key]: null } });
  }

  resetAllSettings(s: ChatSession): void {
    const nulls: Record<string, null> = {};
    for (const k of Object.keys(s.settings_delta || {})) nulls[k] = null;
    if (Object.keys(nulls).length) this.svc.update(s.id, { settings: nulls });
  }

  patchGroup(s: ChatSession, value: string): void {
    if (this.groupDebounce) clearTimeout(this.groupDebounce);
    this.groupDebounce = setTimeout(() => {
      this.svc.update(s.id, { group: value.trim() });
    }, 600);
  }

  patchPromptDebounced(s: ChatSession, value: string): void {
    if (this.promptDebounce) clearTimeout(this.promptDebounce);
    this.promptDebounce = setTimeout(() => {
      this.svc.update(s.id, { system_prompt: value });
    }, 600);
  }

  // ── PUG preset helpers ───────────────────────────────────────────────────

  pugPreset(s: ChatSession): PugPresetName | 'custom' {
    return resolvePugPreset(s.settings);
  }

  pugDescription(s: ChatSession): string {
    return pugPresetDescription(this.pugPreset(s));
  }

  applyPugPreset(s: ChatSession, preset: PugPresetName): void {
    for (const [k, v] of pugPresetSettingEntries(preset)) {
      this.svc.update(s.id, { settings: { [k]: v } });
    }
  }

  // ── Utilities ────────────────────────────────────────────────────────────

  formatDate(ts: number): string {
    return new Date(ts * 1000).toLocaleString('de-DE', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }
}
