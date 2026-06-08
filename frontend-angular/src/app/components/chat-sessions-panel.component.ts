import { Component, inject, OnInit } from '@angular/core';
import { CommonModule, AsyncPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ChatSessionsService, ChatSession, CreateSessionPayload } from '../services/chat-sessions.service';

@Component({
  selector: 'app-chat-sessions-panel',
  standalone: true,
  imports: [CommonModule, AsyncPipe, FormsModule],
  template: `
    <div class="sessions-panel">

      <!-- ── Session list ── -->
      <div class="list">
        @for (s of (svc.sessions$ | async) || []; track s.id) {
          <div class="session-row" [class.active]="s.id === (svc.activeSessionId$ | async)">

            @if (editingId === s.id) {
              <!-- Rename inline -->
              <input class="name-input" [(ngModel)]="editName"
                     (keydown.enter)="saveEdit(s)" (keydown.escape)="cancelEdit()" />
              <button class="icon-btn ok" (click)="saveEdit(s)" title="Speichern">✓</button>
              <button class="icon-btn"    (click)="cancelEdit()"  title="Abbrechen">✕</button>
            } @else {
              <!-- Session name button → activate -->
              <button class="session-btn" (click)="activate(s)"
                      title="Session aktivieren (wird beim Chatten benutzt)">
                <span class="sess-icon">{{ s.icon || '💬' }}</span>
                <span class="sess-name">{{ s.name }}</span>
                @if (s.id === (svc.activeSessionId$ | async)) {
                  <span class="active-dot" title="Aktiv">●</span>
                }
              </button>

              <!-- Gear → toggle settings panel -->
              <button class="icon-btn cfg" [class.cfg-open]="expandedId === s.id"
                      (click)="toggleSettings(s)" title="Einstellungen">⚙</button>
              <!-- Rename -->
              <button class="icon-btn" (click)="startEdit(s)" title="Umbenennen">✎</button>
              <!-- Delete -->
              <button class="icon-btn del"
                      (click)="confirmDelete(s)"
                      [disabled]="((svc.sessions$ | async) || []).length <= 1"
                      title="Session löschen">✕</button>
            }
          </div>

          <!-- ── Per-session settings panel (opened with ⚙) ── -->
          @if (expandedId === s.id && editingId !== s.id) {
            <div class="cfg-panel">
              <div class="cfg-hint">Änderungen werden sofort gespeichert.</div>

              <label class="cfg-label">Backend
                <select [ngModel]="getStr(s, 'chat_backend', 'ananta-worker')"
                        (ngModelChange)="patchSetting(s, 'chat_backend', $event)">
                  <option value="ananta-worker">ananta-worker</option>
                  <option value="opencode">opencode</option>
                  <option value="lmstudio">lmstudio</option>
                  <option value="hermes">hermes</option>
                </select>
              </label>

              <label class="cfg-label">Retrieval-Profil
                <select [ngModel]="getStr(s, 'chat_retrieval_profile', 'auto')"
                        (ngModelChange)="patchSetting(s, 'chat_retrieval_profile', $event)">
                  <option value="auto">auto</option>
                  <option value="code_first">code_first</option>
                  <option value="none">none</option>
                </select>
              </label>

              <label class="cfg-label inline">
                <input type="checkbox"
                       [ngModel]="getBool(s, 'chat_use_codecompass')"
                       (ngModelChange)="patchSetting(s, 'chat_use_codecompass', $event)" />
                CodeCompass aktiv
              </label>

              <label class="cfg-label inline">
                <input type="checkbox"
                       [ngModel]="getBool(s, 'chat_code_questions_repo_first')"
                       (ngModelChange)="patchSetting(s, 'chat_code_questions_repo_first', $event)" />
                Code-Fragen: Repo bevorzugen
              </label>

              <label class="cfg-label">
                System-Prompt
                <textarea rows="4"
                          [ngModel]="s.system_prompt"
                          (ngModelChange)="patchPromptDebounced(s, $event)"
                          placeholder="Leer = Standard-Prompt des Systems"></textarea>
              </label>

              <button class="close-cfg-btn" (click)="expandedId = ''">Schließen ▲</button>
            </div>
          }
        }
      </div>

      <!-- ── "New session" toggle ── -->
      <div class="bottom-bar">
        <button class="new-btn" (click)="showNew = !showNew">
          {{ showNew ? '▲ Abbrechen' : '＋ Neue Session' }}
        </button>
      </div>

      <!-- ── New session form ── -->
      @if (showNew) {
        <div class="new-form">
          <div class="new-form-row">
            <input [(ngModel)]="newIcon" placeholder="🤖" maxlength="4" class="icon-field" />
            <input [(ngModel)]="newName" placeholder="Name der Session *" class="name-field"
                   (keydown.enter)="createNew()" />
          </div>

          <label class="cfg-label">Backend
            <select [(ngModel)]="newBackend">
              <option value="ananta-worker">ananta-worker</option>
              <option value="opencode">opencode</option>
              <option value="lmstudio">lmstudio</option>
              <option value="hermes">hermes</option>
            </select>
          </label>

          <label class="cfg-label inline">
            <input type="checkbox" [(ngModel)]="newCodeCompass" /> CodeCompass aktiv
          </label>

          <label class="cfg-label">
            System-Prompt (optional)
            <textarea rows="3" [(ngModel)]="newPrompt"
                      placeholder="z.B. Du bist ein Python-Experte. Antworte auf Deutsch."></textarea>
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

    /* ── Session rows ── */
    .list { display: flex; flex-direction: column; }
    .session-row {
      display: flex; align-items: center; gap: 3px;
      padding: 2px 6px; border-bottom: 1px solid #152040;
    }
    .session-row.active { background: #0e2038; }

    .session-btn {
      flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px;
      background: transparent; border: none; color: #c8d8f8; padding: 6px 4px;
      cursor: pointer; text-align: left; font-size: 12px; border-radius: 2px;
    }
    .session-btn:hover { color: #7fffd4; }
    .session-row.active .session-btn { color: #7fffd4; font-weight: 500; }

    .sess-icon { font-size: 13px; flex-shrink: 0; }
    .sess-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .active-dot { color: #7fffd4; font-size: 7px; flex-shrink: 0; }

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
      padding: 8px 10px 10px;
      background: #08131f;
      border-bottom: 1px solid #1a3050;
      display: flex; flex-direction: column; gap: 7px;
    }
    .cfg-hint { font-size: 10px; color: #3a6a9a; margin-bottom: 2px; }
    .cfg-label {
      display: flex; flex-direction: column; gap: 3px;
      font-size: 11px; color: #6b8ab8;
    }
    .cfg-label.inline { flex-direction: row; align-items: center; gap: 7px; color: #c8d8f8; }
    select, textarea {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      padding: 4px 6px; font-family: inherit; font-size: 11px; border-radius: 2px;
    }
    textarea { resize: vertical; }
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
      padding: 10px;
      background: #08131f;
      border-top: 1px solid #1a2d4a;
      display: flex; flex-direction: column; gap: 8px;
    }
    .new-form-row { display: flex; gap: 6px; }
    .icon-field { width: 46px; flex-shrink: 0; background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8; padding: 4px 6px; font-size: 13px; border-radius: 2px; }
    .name-field { flex: 1; background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8; padding: 4px 6px; font-size: 12px; font-family: inherit; border-radius: 2px; }
    .name-field::placeholder { color: #3a5a8a; }
    .create-btn {
      background: #102238; border: 1px solid #2a5090; color: #7fffd4;
      padding: 6px 10px; cursor: pointer; font-size: 12px; border-radius: 2px;
    }
    .create-btn:disabled { opacity: 0.35; cursor: default; }
    .create-btn:not(:disabled):hover { background: #183250; }

    .err { color: #fb7185; font-size: 11px; padding: 5px 10px; }
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
  newPrompt = '';
  newBackend = 'ananta-worker';
  newCodeCompass = true;

  private promptDebounce: ReturnType<typeof setTimeout> | null = null;

  ngOnInit(): void {
    this.svc.load();
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

  cancelEdit(): void {
    this.editingId = '';
  }

  createNew(): void {
    const name = this.newName.trim();
    if (!name) return;
    const payload: CreateSessionPayload = {
      name,
      icon: this.newIcon || '💬',
      system_prompt: this.newPrompt,
      settings: {
        chat_backend: this.newBackend,
        chat_use_codecompass: this.newCodeCompass,
      },
    };
    this.svc.create(payload);
    this.newName = '';
    this.newIcon = '💬';
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
    return String(s.settings?.[key] ?? fallback);
  }

  getBool(s: ChatSession, key: string): boolean {
    return !!s.settings?.[key];
  }

  patchSetting(s: ChatSession, key: string, value: unknown): void {
    this.svc.update(s.id, { settings: { ...s.settings, [key]: value } });
  }

  patchPromptDebounced(s: ChatSession, value: string): void {
    if (this.promptDebounce) clearTimeout(this.promptDebounce);
    this.promptDebounce = setTimeout(() => {
      this.svc.update(s.id, { system_prompt: value });
    }, 600);
  }
}
