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
} from '../services/chat-sessions.service';
import { ChatHistoryService } from '../services/chat-history.service';
import { ChatOrganizationDialogsComponent } from './chat-organization-dialogs.component';
import { ChatProfileEditorComponent } from './chat-profile-editor.component';

const PUG_PRESETS = {
  quiet:    { predictive_guide_dwell_ms: 5000, predictive_guide_min_confidence: 0.7,  predictive_guide_multi_candidates: 1 },
  balanced: { predictive_guide_dwell_ms: 1500, predictive_guide_min_confidence: 0.55, predictive_guide_multi_candidates: 3 },
  eager:    { predictive_guide_dwell_ms: 800,  predictive_guide_min_confidence: 0.35, predictive_guide_multi_candidates: 5 },
} as const;

const PUG_DESCRIPTIONS: Record<string, string> = {
  quiet:    'dwell=5000ms, confidence=0.7, candidates=1 — Snake reagiert selten, nur bei klaren Änderungen',
  balanced: 'dwell=1500ms, confidence=0.55, candidates=3 — Ausgewogenes Verhalten',
  eager:    'dwell=800ms, confidence=0.35, candidates=5 — Snake reagiert häufig auf jede Änderung',
  custom:   'Individuelle Einstellungen aktiv',
};

interface FolderNode {
  folder: ChatFolder;
  sessions: ChatSession[];
  children: FolderNode[];
}

interface TreeItem {
  trackId: string;
  kind: 'folder-header' | 'session';
  depth: number;
  folder?: FolderNode;
  session?: ChatSession;
  folderId: string;
}

@Component({
  selector: 'app-chat-sessions-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, ChatOrganizationDialogsComponent, ChatProfileEditorComponent],
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

                  <!-- Backend -->
                  <div class="cfg-row">
                    <label class="cfg-label">Backend</label>
                    <select [ngModel]="getStr(s, 'chat_backend', 'ananta-worker')"
                            (ngModelChange)="patchSetting(s, 'chat_backend', $event)">
                      <option value="ananta-worker">ananta-worker</option>
                      <option value="opencode">opencode</option>
                      <option value="lmstudio">lmstudio</option>
                      <option value="hermes">hermes</option>
                    </select>
                    <span class="delta-dot" [class.on]="isOverride(s,'chat_backend')"
                          (click)="resetSetting(s,'chat_backend')"
                          [title]="isOverride(s,'chat_backend') ? 'Zurücksetzen auf Standard' : 'Standard'"
                    >{{ isOverride(s,'chat_backend') ? '●' : '○' }}</span>
                  </div>

                  <!-- RAG-Modus -->
                  <div class="cfg-row">
                    <label class="cfg-label">RAG-Modus</label>
                    <select [ngModel]="getStr(s, 'chat_architecture_analysis_mode', '')"
                            (ngModelChange)="patchSetting(s, 'chat_architecture_analysis_mode', $event || false)">
                      <option value="">aus</option>
                      <option value="rag_iterative">rag_iterative</option>
                    </select>
                    <span class="delta-dot" [class.on]="isOverride(s,'chat_architecture_analysis_mode')"
                          (click)="resetSetting(s,'chat_architecture_analysis_mode')"
                          [title]="isOverride(s,'chat_architecture_analysis_mode') ? 'Zurücksetzen' : 'Standard'"
                    >{{ isOverride(s,'chat_architecture_analysis_mode') ? '●' : '○' }}</span>
                  </div>

                  <!-- Retrieval-Profil -->
                  <div class="cfg-row">
                    <label class="cfg-label">Retrieval</label>
                    <select [ngModel]="getStr(s, 'chat_retrieval_profile', 'auto')"
                            (ngModelChange)="patchSetting(s, 'chat_retrieval_profile', $event)">
                      <option value="auto">auto</option>
                      <option value="code_first">code_first</option>
                      <option value="repo_first">repo_first</option>
                      <option value="none">none</option>
                    </select>
                    <span class="delta-dot" [class.on]="isOverride(s,'chat_retrieval_profile')"
                          (click)="resetSetting(s,'chat_retrieval_profile')"
                          [title]="isOverride(s,'chat_retrieval_profile') ? 'Zurücksetzen' : 'Standard'"
                    >{{ isOverride(s,'chat_retrieval_profile') ? '●' : '○' }}</span>
                  </div>

                  <!-- Antwort-Zeichen -->
                  <div class="cfg-row">
                    <label class="cfg-label">Antwort-Zeichen</label>
                    <input type="number" min="500" max="20000" step="500"
                           [ngModel]="getNum(s, 'chat_answer_chars', 1800)"
                           (ngModelChange)="patchSetting(s, 'chat_answer_chars', +$event)" />
                    <span class="delta-dot" [class.on]="isOverride(s,'chat_answer_chars')"
                          (click)="resetSetting(s,'chat_answer_chars')"
                          [title]="isOverride(s,'chat_answer_chars') ? 'Zurücksetzen' : 'Standard'"
                    >{{ isOverride(s,'chat_answer_chars') ? '●' : '○' }}</span>
                  </div>

                  <!-- Max Tokens -->
                  <div class="cfg-row">
                    <label class="cfg-label">Max. Tokens</label>
                    <input type="number" min="512" max="16000" step="512"
                           [ngModel]="getNum(s, 'chat_max_tokens', 4000)"
                           (ngModelChange)="patchSetting(s, 'chat_max_tokens', +$event)" />
                    <span class="delta-dot" [class.on]="isOverride(s,'chat_max_tokens')"
                          (click)="resetSetting(s,'chat_max_tokens')"
                          [title]="isOverride(s,'chat_max_tokens') ? 'Zurücksetzen' : 'Standard'"
                    >{{ isOverride(s,'chat_max_tokens') ? '●' : '○' }}</span>
                  </div>

                  <!-- Checkboxen -->
                  <div class="cfg-checkboxes">
                    <label class="cfg-check" [class.overridden]="isOverride(s,'chat_use_codecompass')">
                      <input type="checkbox"
                             [ngModel]="getBool(s, 'chat_use_codecompass')"
                             (ngModelChange)="patchSetting(s, 'chat_use_codecompass', $event)" />
                      CodeCompass
                      <span class="delta-dot-inline" [class.on]="isOverride(s,'chat_use_codecompass')"
                            (click)="$event.preventDefault(); resetSetting(s,'chat_use_codecompass')"
                      >{{ isOverride(s,'chat_use_codecompass') ? '●' : '○' }}</span>
                    </label>
                    <label class="cfg-check" [class.overridden]="isOverride(s,'chat_code_questions_repo_first')">
                      <input type="checkbox"
                             [ngModel]="getBool(s, 'chat_code_questions_repo_first')"
                             (ngModelChange)="patchSetting(s, 'chat_code_questions_repo_first', $event)" />
                      Repo bevorzugen
                      <span class="delta-dot-inline" [class.on]="isOverride(s,'chat_code_questions_repo_first')"
                            (click)="$event.preventDefault(); resetSetting(s,'chat_code_questions_repo_first')"
                      >{{ isOverride(s,'chat_code_questions_repo_first') ? '●' : '○' }}</span>
                    </label>
                    <label class="cfg-check" [class.overridden]="isOverride(s,'chat_include_wikipedia')">
                      <input type="checkbox"
                             [ngModel]="getBool(s, 'chat_include_wikipedia')"
                             (ngModelChange)="patchSetting(s, 'chat_include_wikipedia', $event)" />
                      Wikipedia
                      <span class="delta-dot-inline" [class.on]="isOverride(s,'chat_include_wikipedia')"
                            (click)="$event.preventDefault(); resetSetting(s,'chat_include_wikipedia')"
                      >{{ isOverride(s,'chat_include_wikipedia') ? '●' : '○' }}</span>
                    </label>
                  </div>

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

                    <!-- Verlauf row -->
                    <div class="ctx-row">
                      <span class="ctx-row-label">Verlauf</span>
                      <label class="ctx-toggle-lbl">
                        <input type="checkbox"
                               [ngModel]="getBool(s, 'chat_use_history')"
                               (ngModelChange)="patchSetting(s, 'chat_use_history', $event)" />
                        {{ getBool(s, 'chat_use_history') ? '● aktiv' : '○ inaktiv' }}
                      </label>
                      <input type="number" class="ctx-num" min="1" max="200"
                             [ngModel]="getNum(s, 'chat_history_turns', 20)"
                             (ngModelChange)="patchSetting(s, 'chat_history_turns', +$event)"
                             title="Max. Turns" />
                      <span class="ctx-unit">Turns, max.</span>
                      <input type="number" class="ctx-num wide" min="1000" max="200000" step="1000"
                             [ngModel]="getNum(s, 'chat_history_chars', 20000)"
                             (ngModelChange)="patchSetting(s, 'chat_history_chars', +$event)"
                             title="Max. Zeichen" />
                      <span class="ctx-unit">Zeichen</span>
                    </div>

                    <!-- Zusammenfassung row -->
                    <div class="ctx-row">
                      <span class="ctx-row-label">Zusammenfassung</span>
                      <label class="ctx-toggle-lbl">
                        <input type="checkbox"
                               [ngModel]="getBool(s, 'chat_use_summary')"
                               (ngModelChange)="patchSetting(s, 'chat_use_summary', $event)" />
                        {{ getBool(s, 'chat_use_summary') ? '● aktiv' : '○ inaktiv' }}
                      </label>
                      <span class="ctx-unit">max.</span>
                      <input type="number" class="ctx-num wide" min="500" max="20000" step="500"
                             [ngModel]="getNum(s, 'chat_summary_chars', 5000)"
                             (ngModelChange)="patchSetting(s, 'chat_summary_chars', +$event)" />
                      <span class="ctx-unit">Zeichen</span>
                    </div>

                    <!-- RAG row -->
                    <div class="ctx-row">
                      <span class="ctx-row-label">RAG</span>
                      <label class="ctx-toggle-lbl">
                        <input type="checkbox"
                               [ngModel]="getBool(s, 'chat_use_rag')"
                               (ngModelChange)="patchSetting(s, 'chat_use_rag', $event)" />
                        {{ getBool(s, 'chat_use_rag') ? '● aktiv' : '○ inaktiv' }}
                      </label>
                      <span class="ctx-unit">Profil:</span>
                      <select class="ctx-select"
                              [ngModel]="getStr(s, 'chat_rag_profile', 'auto')"
                              (ngModelChange)="patchSetting(s, 'chat_rag_profile', $event)">
                        <option value="auto">auto</option>
                        <option value="code_first">code_first</option>
                        <option value="repo_first">repo_first</option>
                      </select>
                      <span class="ctx-unit">Top-K:</span>
                      <input type="number" class="ctx-num" min="1" max="20"
                             [ngModel]="getNum(s, 'rag_top_k', 5)"
                             (ngModelChange)="patchSetting(s, 'rag_top_k', +$event)" />
                    </div>

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
  styles: [`
    :host { display: block; }
    .sessions-panel { display: flex; flex-direction: column; }

    /* ── Top bar ── */
    .top-bar {
      display: flex; align-items: center; gap: 6px;
      padding: 5px 8px; border-bottom: 1px solid #0d1c30;
    }
    .profile-manager { padding: 8px; border-bottom: 1px solid #1a2d4a; background: #091526; display: grid; gap: 6px; }
    .profile-row { display: grid; grid-template-columns: 1fr auto auto auto; align-items: center; gap: 5px; font-size: 11px; }
    .profile-editor { display: grid; grid-template-columns: auto 1fr; gap: 6px; padding-top: 6px; border-top: 1px dashed #1a2d4a; }
    .profile-editor textarea, .profile-editor .row { grid-column: 1 / -1; }
    .reorg-btn {
      background: #0a1825; border: 1px solid #1a3050; color: #7ab0e0;
      padding: 4px 10px; cursor: pointer; font-size: 11px; border-radius: 3px; flex: 1;
    }
    .reorg-btn:hover:not(:disabled) { background: #102238; color: #7fffd4; border-color: #2a5090; }
    .reorg-btn:disabled { opacity: 0.45; cursor: default; }
    .new-folder-btn {
      background: #0a1825; border: 1px solid #1a3050; color: #7ab0e0;
      padding: 4px 8px; cursor: pointer; font-size: 11px; border-radius: 3px; flex-shrink: 0;
    }
    .new-folder-btn:hover { background: #102238; color: #7fffd4; border-color: #2a5090; }

    /* ── Root drop zone ── */
    .root-drop-zone {
      border: 1px dashed #3a6a9a; padding: 6px; font-size: 10px;
      color: #6b8ab8; text-align: center; margin: 4px 6px; border-radius: 3px;
    }
    .root-drop-zone.drag-over { border-color: #3aacca; background: #0a2030; }
    .loading-indicator { color: #3a6a9a; font-size: 13px; animation: spin 1s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ── Folder tree ── */
    .list { display: flex; flex-direction: column; }
    .folder-row {
      display: flex; align-items: center; gap: 4px;
      padding: 4px 6px; cursor: pointer; border-bottom: 1px solid #0d1c30;
    }
    .folder-row:hover { background: #0a1825; }
    .folder-row.drag-over { background: #0a2030; border: 1px dashed #3aacca; }
    .folder-chevron { font-size: 9px; color: #3a6a9a; width: 12px; flex-shrink: 0; }
    .folder-icon { font-size: 13px; flex-shrink: 0; }
    .folder-name {
      flex: 1; font-size: 11px; color: #7ab0e0;
      min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }

    /* ── Session rows ── */
    .session-row {
      display: flex; align-items: center; gap: 3px;
      padding: 2px 6px; border-bottom: 1px solid #152040;
    }
    .session-row.active { background: #0e2038; }
    .session-row[draggable] { cursor: grab; }
    .session-row.drag-over { background: #0a2030; border: 1px dashed #3aacca; }

    /* ── Icon wrap + tooltip ── */
    .icon-wrap { position: relative; display: inline-flex; align-items: center; flex-shrink: 0; }
    .session-tooltip {
      position: absolute; left: 22px; top: 0; z-index: 100;
      background: #071624; border: 1px solid #1a3a5a; border-radius: 4px;
      padding: 7px 10px; min-width: 180px; max-width: 240px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.5); pointer-events: none;
    }
    .tt-name { font-weight: 600; color: #7fffd4; font-size: 12px; margin-bottom: 3px; }
    .tt-type { font-size: 10px; color: #3aacca; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }
    .tt-desc { font-size: 10px; color: #8aaccc; line-height: 1.4; margin-bottom: 4px; }
    .tt-preview { font-size: 10px; color: #6a8aaa; font-style: italic; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .tt-muted { color: #3a5a7a; font-size: 10px; }
    .tt-row { font-size: 10px; color: #8aaac8; margin-bottom: 2px; }

    .session-btn {
      flex: 1; min-width: 0; display: flex; align-items: center; gap: 5px;
      background: transparent; border: none; color: #c8d8f8; padding: 6px 4px;
      cursor: pointer; text-align: left; font-size: 12px; border-radius: 2px;
    }
    .session-btn:hover { color: #7fffd4; }
    .session-row.active .session-btn { color: #7fffd4; font-weight: 500; }

    .sess-icon { font-size: 13px; flex-shrink: 0; }
    .sess-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .active-dot { color: #7fffd4; font-size: 7px; flex-shrink: 0; }
    .delta-badge {
      background: #0a2a3a; border: 1px solid #1a5a7a; color: #3aacca;
      font-size: 9px; padding: 1px 4px; border-radius: 8px; flex-shrink: 0;
    }

    /* ── Icon buttons ── */
    .icon-btn {
      background: transparent; border: none; color: #2a4a6a; padding: 3px 5px;
      cursor: pointer; font-size: 12px; flex-shrink: 0; border-radius: 2px;
    }
    .icon-btn:hover:not(:disabled) { color: #c8d8f8; background: #102030; }
    .icon-btn.del:hover:not(:disabled) { color: #fb7185; background: #1a0a0a; }
    .icon-btn.ok:hover { color: #7fffd4; }
    .icon-btn:disabled { opacity: 0.25; cursor: default; }
    .icon-btn.cfg { font-size: 13px; }
    .icon-btn.cfg.cfg-open { color: #7fffd4; }

    /* ── Rename input ── */
    .name-input {
      flex: 1; background: #0f1c30; border: 1px solid #2a4070; color: #c8d8f8;
      padding: 3px 6px; font-size: 12px; font-family: inherit; border-radius: 2px;
    }

    /* ── Per-session config panel ── */
    .cfg-panel {
      padding: 8px 10px 10px; background: #08131f;
      border-bottom: 1px solid #1a3050;
      display: flex; flex-direction: column; gap: 6px;
    }
    .cfg-hint {
      font-size: 10px; color: #3a6a9a; display: flex; align-items: center; gap: 6px;
    }
    .cfg-hint-badge {
      background: #0a2a3a; border: 1px solid #1a4a6a; color: #3a8aaa;
      font-size: 9px; padding: 1px 5px; border-radius: 8px;
    }

    /* ── Setting row: label + control + delta dot ── */
    .cfg-row {
      display: grid; grid-template-columns: 90px 1fr 16px; gap: 5px; align-items: center;
    }
    .cfg-label { font-size: 11px; color: #6b8ab8; white-space: nowrap; }
    .cfg-label-block {
      display: flex; flex-direction: column; gap: 3px;
      font-size: 11px; color: #6b8ab8;
    }
    .cfg-label-block.inline { flex-direction: row; align-items: center; gap: 7px; color: #c8d8f8; }

    select, input[type="text"], input[type="number"], textarea {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      padding: 3px 5px; font-family: inherit; font-size: 11px; border-radius: 2px;
    }
    textarea { resize: vertical; }

    /* ── Delta dot: ○ inherited, ● overridden ── */
    .delta-dot {
      color: #2a4a6a; font-size: 11px; cursor: pointer; user-select: none;
      justify-self: center; transition: color 0.15s;
    }
    .delta-dot.on { color: #3aacca; }
    .delta-dot.on:hover { color: #fb7185; }

    /* ── Checkboxes with inline delta dot ── */
    .cfg-checkboxes { display: flex; flex-direction: column; gap: 4px; }
    .cfg-check {
      display: flex; align-items: center; gap: 6px;
      font-size: 11px; color: #c8d8f8; cursor: pointer;
    }
    .cfg-check.overridden { color: #a8c8f0; }
    .delta-dot-inline {
      color: #2a4a6a; font-size: 10px; cursor: pointer; margin-left: auto;
    }
    .delta-dot-inline.on { color: #3aacca; }
    .delta-dot-inline.on:hover { color: #fb7185; }

    .reset-all-btn {
      background: #0a1a2a; border: 1px solid #1a3a5a; color: #3a8aaa;
      padding: 3px 8px; cursor: pointer; font-size: 10px; border-radius: 2px;
      align-self: flex-start;
    }
    .reset-all-btn:hover { color: #fb7185; border-color: #4a1a1a; }
    .close-cfg-btn {
      background: transparent; border: 1px solid #1a2d4a; color: #4a6a9a;
      padding: 3px 8px; cursor: pointer; font-size: 10px; align-self: flex-end;
      border-radius: 2px;
    }
    .close-cfg-btn:hover { color: #c8d8f8; }

    /* ── PUG preset section ── */
    .pug-section {
      background: #07111e; border: 1px solid #1a3050; border-radius: 3px;
      padding: 7px 9px; display: flex; flex-direction: column; gap: 5px;
    }
    .pug-title { font-size: 10px; color: #3a7aaa; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; }
    .pug-preset-bar { display: flex; gap: 4px; align-items: center; }
    .pug-btn {
      background: transparent; border: 1px solid #1a3050; color: #4a6a9a;
      padding: 2px 8px; cursor: pointer; font-size: 10px; border-radius: 2px;
    }
    .pug-btn:hover { color: #c8d8f8; }
    .pug-btn.active { color: #7fffd4; border-color: #2a6a7a; background: #0a2030; }
    .pug-custom { font-size: 10px; color: #7a5a3a; border: 1px solid #3a2a1a; padding: 2px 6px; border-radius: 2px; }
    .pug-desc { font-size: 10px; color: #4a6a8a; line-height: 1.5; }

    /* ── Context / History section ── */
    .ctx-section {
      background: #07111e; border: 1px solid #1a3050; border-radius: 3px;
      padding: 7px 9px; display: flex; flex-direction: column; gap: 5px;
    }
    .ctx-title { font-size: 10px; color: #3a7aaa; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; }
    .ctx-row { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
    .ctx-row-label { font-size: 10px; color: #6b8ab8; width: 88px; flex-shrink: 0; }
    .ctx-toggle-lbl {
      font-size: 10px; color: #8aaac8; display: flex; align-items: center; gap: 4px; cursor: pointer;
    }
    .ctx-num {
      width: 46px; padding: 2px 4px; font-size: 10px;
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      border-radius: 2px; font-family: inherit;
    }
    .ctx-num.wide { width: 64px; }
    .ctx-unit { font-size: 10px; color: #4a6a8a; white-space: nowrap; }
    .ctx-select {
      font-size: 10px; padding: 2px 4px;
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      border-radius: 2px; font-family: inherit;
    }
    .ctx-btn-row { display: flex; gap: 5px; margin-top: 2px; }
    .ctx-overview-btn {
      background: transparent; border: 1px solid #1a2d4a; color: #4a6a9a;
      padding: 2px 8px; cursor: pointer; font-size: 10px; align-self: flex-start;
      border-radius: 2px; margin-top: 2px;
    }
    .ctx-btn-row .ctx-overview-btn { margin-top: 0; }
    .ctx-overview-btn:hover { color: #7fffd4; border-color: #2a5080; }
    .ctx-table { font-size: 10px; border-collapse: collapse; width: 100%; margin-top: 4px; }
    .ctx-td-label { color: #6b8ab8; padding: 2px 6px 2px 0; width: 100px; white-space: nowrap; }
    .ctx-td-val { color: #9ab8d8; padding: 2px 0; }
    .ctx-loading { font-size: 10px; color: #3a6a9a; padding: 4px 0; }
    .ctx-clear-btn {
      background: transparent; border: 1px solid #2a1a1a; color: #7a4a4a;
      padding: 2px 8px; cursor: pointer; font-size: 10px; align-self: flex-start;
      border-radius: 2px; margin-top: 2px;
    }
    .ctx-clear-btn:hover { color: #fb7185; border-color: #4a1a1a; }

    /* ── Bottom bar + new form ── */
    .bottom-bar { padding: 6px 8px; border-top: 1px solid #152040; }
    .new-btn {
      background: transparent; border: 1px solid #1a2d4a; color: #6b8ab8;
      padding: 5px 10px; cursor: pointer; font-size: 11px; width: 100%; border-radius: 2px;
    }
    .new-btn:hover { color: #c8d8f8; border-color: #2a4070; }

    .new-form {
      padding: 10px; background: #08131f; border-top: 1px solid #1a2d4a;
      display: flex; flex-direction: column; gap: 7px;
    }
    .new-form-label { font-size: 10px; color: #6b8ab8; text-transform: uppercase; letter-spacing: 0.05em; }
    .new-form-row { display: flex; gap: 6px; }
    .icon-field { width: 46px; flex-shrink: 0; }
    .name-field { flex: 1; }
    .group-field { flex: 1; }
    .create-btn {
      background: #102238; border: 1px solid #2a5090; color: #7fffd4;
      padding: 6px 10px; cursor: pointer; font-size: 12px; border-radius: 2px;
    }
    .create-btn:disabled { opacity: 0.35; cursor: default; }
    .create-btn:not(:disabled):hover { background: #183250; }

    /* ── Session type grid ── */
    .type-grid {
      display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px;
    }
    .type-btn {
      display: flex; flex-direction: column; align-items: center; gap: 2px;
      background: #07111e; border: 1px solid #1a2d4a; color: #6b8ab8;
      padding: 5px 4px; cursor: pointer; border-radius: 3px; font-size: 10px;
    }
    .type-btn:hover { background: #0a1825; border-color: #2a4070; color: #c8d8f8; }
    .type-btn.selected { border-color: #2a6a7a; background: #0a2030; color: #7fffd4; }
    .type-icon { font-size: 14px; }
    .type-label { font-size: 9px; white-space: nowrap; }

    .err { color: #fb7185; font-size: 11px; padding: 5px 10px; }

    /* ── AI Reorganize modal ── */
    .modal-backdrop {
      position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 200;
      display: flex; align-items: center; justify-content: center;
    }
    .modal {
      background: #07111e; border: 1px solid #1a3050; border-radius: 6px;
      padding: 20px; min-width: 360px; max-width: 500px; max-height: 80vh; overflow-y: auto;
    }
    .modal-title { font-size: 14px; font-weight: 600; color: #7fffd4; margin-bottom: 12px; }
    .modal-summary { font-size: 11px; color: #6b8ab8; margin-bottom: 14px; line-height: 1.5; }
    .proposal-folder { margin-bottom: 10px; }
    .proposal-folder-header { font-size: 12px; color: #7ab0e0; font-weight: 500; margin-bottom: 4px; }
    .proposal-session-list { padding-left: 14px; }
    .proposal-session-item { font-size: 11px; color: #9ab8d8; padding: 2px 0; }
    .modal-actions { display: flex; gap: 8px; margin-top: 16px; justify-content: flex-end; }
    .btn-accept {
      background: #102238; border: 1px solid #2a5090; color: #7fffd4;
      padding: 6px 14px; cursor: pointer; font-size: 12px; border-radius: 3px;
    }
    .btn-accept:hover { background: #183250; }
    .btn-cancel {
      background: transparent; border: 1px solid #1a2d4a; color: #4a6a9a;
      padding: 6px 14px; cursor: pointer; font-size: 12px; border-radius: 3px;
    }
    .btn-cancel:hover { color: #c8d8f8; }

    /* ── Prompt preview modal ── */
    .preview-modal { min-width: 420px; max-width: 620px; }
    .preview-sections { display: flex; flex-direction: column; gap: 3px; margin-bottom: 10px; }
    .preview-sec-row { display: flex; align-items: center; gap: 8px; font-size: 11px; }
    .preview-sec-name { color: #c8d8f8; width: 130px; flex-shrink: 0; }
    .preview-badge {
      font-size: 9px; padding: 1px 6px; border-radius: 8px;
      background: #101a28; border: 1px solid #1a2d4a; color: #4a6a8a;
    }
    .preview-badge.on { background: #0a2a1a; border-color: #2a5a4a; color: #7fffd4; }
    .preview-chars { color: #6b8ab8; font-size: 10px; }
    .preview-trunc { color: #d8a848; font-size: 10px; }
    .preview-total { font-size: 11px; color: #9ab8d8; margin-bottom: 8px; }
    .preview-pre {
      max-height: 300px; overflow-y: auto; font-size: 10px;
      background: #050d18; border: 1px solid #1a2d4a; border-radius: 3px;
      padding: 8px; color: #9ab8d8; white-space: pre-wrap; word-break: break-word;
      margin: 0;
    }
  `],
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
    const ordered = [...folders].sort((a, b) =>
      (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
    const roots = ordered.filter(f => !f.parent_id);
    return roots.map(f => ({
      folder: f,
      sessions: sessions.filter(s => s.folder_id === f.id)
        .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name)),
      children: this.buildFolderTree(ordered.filter(c => c.parent_id === f.id), sessions),
    }));
  }

  getFlatTree(): TreeItem[] {
    const items: TreeItem[] = [];

    // Root sessions first (no folder_id)
    for (const s of this.sessions.filter(s => !s.folder_id)) {
      items.push({ trackId: 'sess-' + s.id, kind: 'session', depth: 0, session: s, folderId: '' });
    }

    // Folder nodes
    const addNode = (node: FolderNode, depth: number) => {
      items.push({ trackId: 'fold-' + node.folder.id, kind: 'folder-header', depth, folder: node, folderId: '' });
      if (!this.collapsedFolders.has(node.folder.id)) {
        for (const s of node.sessions) {
          items.push({ trackId: 'sess-' + s.id, kind: 'session', depth: depth + 1, session: s, folderId: node.folder.id });
        }
        for (const child of node.children) {
          addNode(child, depth + 1);
        }
      }
    };

    for (const node of this.buildFolderTree(this.folders, this.sessions)) {
      addNode(node, 0);
    }

    return items;
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
    let current = this.folders.find(folder => folder.id === candidateId);
    const seen = new Set<string>();
    while (current?.parent_id && !seen.has(current.id)) {
      if (current.parent_id === ancestorId) return true;
      seen.add(current.id);
      current = this.folders.find(folder => folder.id === current?.parent_id);
    }
    return false;
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

  patchSetting(s: ChatSession, key: string, value: unknown): void {
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

  pugPreset(s: ChatSession): 'quiet' | 'balanced' | 'eager' | 'custom' {
    for (const [name, vals] of Object.entries(PUG_PRESETS) as Array<[string, Record<string, unknown>]>) {
      const matches = Object.entries(vals).every(([k, v]) => s.settings?.[k] === v || (!s.settings?.[k] && !v));
      if (matches) return name as 'quiet' | 'balanced' | 'eager';
    }
    const hasPugSettings = Object.keys(s.settings || {}).some(
      k => k.startsWith('predictive_guide_dwell') || k.startsWith('predictive_guide_min'),
    );
    if (!hasPugSettings) return 'balanced';
    return 'custom';
  }

  pugDescription(s: ChatSession): string {
    return PUG_DESCRIPTIONS[this.pugPreset(s)] ?? '';
  }

  applyPugPreset(s: ChatSession, preset: 'quiet' | 'balanced' | 'eager'): void {
    const vals = PUG_PRESETS[preset];
    for (const [k, v] of Object.entries(vals)) {
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
