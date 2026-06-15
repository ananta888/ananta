import { Component, inject, OnInit } from '@angular/core';
import { CommonModule, AsyncPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ChatSessionsService, ChatSession, CreateSessionPayload } from '../services/chat-sessions.service';

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

interface SessionGroup { name: string; sessions: ChatSession[]; }

@Component({
  selector: 'app-chat-sessions-panel',
  standalone: true,
  imports: [CommonModule, AsyncPipe, FormsModule],
  template: `
    <div class="sessions-panel">

      <!-- ── Session list grouped ── -->
      <div class="list">
        @for (grp of computeGroups((svc.sessions$ | async) || []); track grp.name) {
          @if (grp.name) {
            <div class="group-header">{{ grp.name }}</div>
          }
          @for (s of grp.sessions; track s.id) {
            <div class="session-row" [class.active]="s.id === (svc.activeSessionId$ | async)"
                 [class.grouped]="!!grp.name">

              @if (editingId === s.id) {
                <input class="name-input" [(ngModel)]="editName"
                       (keydown.enter)="saveEdit(s)" (keydown.escape)="cancelEdit()" />
                <button class="icon-btn ok" (click)="saveEdit(s)" title="Speichern">✓</button>
                <button class="icon-btn"    (click)="cancelEdit()"  title="Abbrechen">✕</button>
              } @else {
                <button class="session-btn" (click)="activate(s)">
                  <span class="sess-icon">{{ s.icon || '💬' }}</span>
                  <span class="sess-name">{{ s.name }}</span>
                  @if (s.id === (svc.activeSessionId$ | async)) {
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
                        [disabled]="((svc.sessions$ | async) || []).length <= 1"
                        title="Session löschen">✕</button>
              }
            </div>

            <!-- ── Per-session settings panel ── -->
            @if (expandedId === s.id && editingId !== s.id) {
              <div class="cfg-panel" [class.cfg-grouped]="!!grp.name">
                <div class="cfg-hint">
                  Nur abweichende Werte werden gespeichert.
                  <span class="cfg-hint-badge">{{ deltaCount(s) }} Override(s)</span>
                </div>

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
      </div>

      <!-- ── New session form ── -->
      <div class="bottom-bar">
        <button class="new-btn" (click)="showNew = !showNew">
          {{ showNew ? '▲ Abbrechen' : '＋ Neue Session' }}
        </button>
      </div>

      @if (showNew) {
        <div class="new-form">
          <div class="new-form-row">
            <input [(ngModel)]="newIcon" placeholder="🤖" maxlength="4" class="icon-field" />
            <input [(ngModel)]="newName" placeholder="Name *" class="name-field"
                   (keydown.enter)="createNew()" />
          </div>
          <div class="new-form-row">
            <input [(ngModel)]="newGroup" placeholder="Gruppe (optional)" class="group-field" />
          </div>
          <label class="cfg-label-block">Backend
            <select [(ngModel)]="newBackend">
              <option value="ananta-worker">ananta-worker</option>
              <option value="opencode">opencode</option>
              <option value="lmstudio">lmstudio</option>
              <option value="hermes">hermes</option>
            </select>
          </label>
          <label class="cfg-label-block inline">
            <input type="checkbox" [(ngModel)]="newCodeCompass" /> CodeCompass aktiv
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

      @if (svc.error$ | async; as e) {
        @if (e) { <div class="err">{{ e }}</div> }
      }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .sessions-panel { display: flex; flex-direction: column; }

    /* ── Groups ── */
    .group-header {
      padding: 4px 10px 2px;
      font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase;
      color: #3a6a9a; background: #07111e; border-bottom: 1px solid #0f2035;
      user-select: none;
    }

    /* ── Session rows ── */
    .list { display: flex; flex-direction: column; }
    .session-row {
      display: flex; align-items: center; gap: 3px;
      padding: 2px 6px; border-bottom: 1px solid #152040;
    }
    .session-row.grouped { padding-left: 14px; }
    .session-row.active { background: #0e2038; }

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
    .cfg-panel.cfg-grouped { padding-left: 18px; }
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

    .err { color: #fb7185; font-size: 11px; padding: 5px 10px; }

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
  `],
})
export class ChatSessionsPanelComponent implements OnInit {
  readonly svc = inject(ChatSessionsService);

  editingId = '';
  editName = '';
  expandedId = '';
  showNew = false;

  newName = '';
  newIcon = '💬';
  newGroup = '';
  newPrompt = '';
  newBackend = 'ananta-worker';
  newCodeCompass = true;

  private promptDebounce: ReturnType<typeof setTimeout> | null = null;
  private groupDebounce: ReturnType<typeof setTimeout> | null = null;

  ngOnInit(): void {
    this.svc.load();
  }

  computeGroups(sessions: ChatSession[]): SessionGroup[] {
    const map = new Map<string, ChatSession[]>();
    for (const s of sessions) {
      const g = s.group || '';
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(s);
    }
    const result: SessionGroup[] = [];
    if (map.has('')) result.push({ name: '', sessions: map.get('')! });
    for (const [name, list] of [...map.entries()].filter(([k]) => k).sort((a, b) => a[0].localeCompare(b[0]))) {
      result.push({ name, sessions: list });
    }
    return result;
  }

  deltaCount(s: ChatSession): number {
    return Object.keys(s.settings_delta || {}).length;
  }

  isOverride(s: ChatSession, key: string): boolean {
    return key in (s.settings_delta || {});
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
      system_prompt: this.newPrompt,
      settings: {
        chat_backend: this.newBackend,
        chat_use_codecompass: this.newCodeCompass,
      },
    };
    this.svc.create(payload);
    this.newName = '';
    this.newIcon = '💬';
    this.newGroup = '';
    this.newPrompt = '';
    this.newBackend = 'ananta-worker';
    this.newCodeCompass = true;
    this.showNew = false;
  }

  confirmDelete(s: ChatSession): void {
    if (confirm(`Session "${s.name}" wirklich löschen?`)) {
      this.svc.remove(s.id);
    }
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

  pugPreset(s: ChatSession): 'quiet' | 'balanced' | 'eager' | 'custom' {
    for (const [name, vals] of Object.entries(PUG_PRESETS) as Array<[string, Record<string, unknown>]>) {
      const matches = Object.entries(vals).every(([k, v]) => s.settings?.[k] === v || (!s.settings?.[k] && !v));
      if (matches) return name as 'quiet' | 'balanced' | 'eager';
    }
    const hasPugSettings = Object.keys(s.settings || {}).some(k => k.startsWith('predictive_guide_dwell') || k.startsWith('predictive_guide_min'));
    if (!hasPugSettings) return 'balanced'; // balanced is the default
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
}
