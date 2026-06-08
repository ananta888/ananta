import { Component, inject, OnInit } from '@angular/core';
import { CommonModule, AsyncPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ChatSessionsService, ChatSession } from '../services/chat-sessions.service';

@Component({
  selector: 'app-chat-sessions-panel',
  standalone: true,
  imports: [CommonModule, AsyncPipe, FormsModule],
  template: `
    <div class="sessions-panel">
      <div class="list">
        @for (s of (svc.sessions$ | async) || []; track s.id) {
          <div class="session-row" [class.active]="s.id === (svc.activeSessionId$ | async)">
            @if (editingId === s.id) {
              <input
                class="name-input"
                [(ngModel)]="editName"
                (keydown.enter)="saveEdit(s)"
                (keydown.escape)="cancelEdit()"
                autofocus
              />
              <button class="icon-btn ok" (click)="saveEdit(s)" title="Speichern">✓</button>
              <button class="icon-btn" (click)="cancelEdit()" title="Abbrechen">✕</button>
            } @else {
              <button class="session-btn" (click)="svc.activate(s.id)" [title]="s.system_prompt || s.name">
                <span class="icon">{{ s.icon || '💬' }}</span>
                <span class="session-name">{{ s.name }}</span>
                @if (s.id === (svc.activeSessionId$ | async)) {
                  <span class="dot">●</span>
                }
              </button>
              <button class="icon-btn" (click)="startEdit(s)" title="Umbenennen">✎</button>
              <button
                class="icon-btn del"
                (click)="confirmDelete(s)"
                [disabled]="((svc.sessions$ | async) || []).length <= 1"
                title="Session löschen"
              >✕</button>
            }
          </div>
          @if (expandedId === s.id && editingId !== s.id) {
            <div class="settings-row">
              <label>Backend
                <select [ngModel]="getSettingStr(s, 'chat_backend', 'ananta-worker')" (ngModelChange)="patchSetting(s, 'chat_backend', $event)">
                  <option value="ananta-worker">ananta-worker</option>
                  <option value="opencode">opencode</option>
                  <option value="lmstudio">lmstudio</option>
                  <option value="hermes">hermes</option>
                </select>
              </label>
              <label class="inline">
                <input type="checkbox" [ngModel]="getSettingBool(s, 'chat_use_codecompass')" (ngModelChange)="patchSetting(s, 'chat_use_codecompass', $event)" />
                CodeCompass
              </label>
              <label>System-Prompt
                <textarea rows="3" [ngModel]="s.system_prompt" (ngModelChange)="patchPrompt(s, $event)"></textarea>
              </label>
            </div>
          }
        }
      </div>

      <div class="actions">
        <button class="expand-toggle" (click)="toggleExpand()">
          {{ showNew ? '▲ Abbrechen' : '+ Neue Session' }}
        </button>
      </div>

      @if (showNew) {
        <div class="new-form">
          <input [(ngModel)]="newName" placeholder="Name" (keydown.enter)="createNew()" />
          <input [(ngModel)]="newIcon" placeholder="Icon (z.B. 💬)" maxlength="4" class="icon-field" />
          <input [(ngModel)]="newPrompt" placeholder="System-Prompt (optional)" />
          <button (click)="createNew()" [disabled]="!newName.trim()">Erstellen</button>
        </div>
      }

      @if (svc.error$ | async; as e) {
        @if (e) { <div class="error-msg">{{ e }}</div> }
      }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .sessions-panel { display: flex; flex-direction: column; gap: 0; }
    .list { display: flex; flex-direction: column; }

    .session-row {
      display: flex;
      align-items: center;
      gap: 4px;
      padding: 2px 6px;
      border-bottom: 1px solid #152040;
    }
    .session-row.active { background: #102238; }

    .session-btn {
      flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px;
      background: transparent; border: none; color: #c8d8f8; padding: 5px 4px;
      cursor: pointer; text-align: left; font-size: 12px;
    }
    .session-btn:hover { color: #7fffd4; }
    .session-row.active .session-btn { color: #7fffd4; }

    .icon { font-size: 13px; flex-shrink: 0; }
    .session-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .dot { color: #7fffd4; font-size: 8px; flex-shrink: 0; }

    .icon-btn {
      background: transparent; border: none; color: #3a5a8a; padding: 3px 5px;
      cursor: pointer; font-size: 11px; flex-shrink: 0;
    }
    .icon-btn:hover { color: #c8d8f8; }
    .icon-btn.del:hover { color: #fb7185; }
    .icon-btn.ok:hover { color: #7fffd4; }
    .icon-btn:disabled { opacity: 0.3; cursor: default; }

    .name-input {
      flex: 1; background: #0f1c30; border: 1px solid #2a4070; color: #c8d8f8;
      padding: 3px 6px; font-size: 12px; font-family: inherit;
    }

    .settings-row {
      padding: 6px 10px 8px;
      background: #0a1520;
      border-bottom: 1px solid #152040;
      display: flex; flex-direction: column; gap: 6px;
    }
    .settings-row label { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: #6b8ab8; }
    .settings-row label.inline { flex-direction: row; align-items: center; gap: 6px; }
    select, textarea, input[type="text"], input:not([type="checkbox"]) {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      padding: 4px 6px; font-family: inherit; font-size: 11px;
    }
    textarea { resize: vertical; }

    .actions { padding: 6px 8px; }
    .expand-toggle {
      background: transparent; border: 1px solid #1a2d4a; color: #6b8ab8;
      padding: 4px 8px; cursor: pointer; font-size: 11px; width: 100%;
    }
    .expand-toggle:hover { color: #c8d8f8; border-color: #2a4070; }

    .new-form {
      padding: 8px;
      background: #0a1520;
      display: flex; flex-direction: column; gap: 6px;
      border-top: 1px solid #1a2d4a;
    }
    .new-form button {
      background: #102238; border: 1px solid #2a4070; color: #7fffd4;
      padding: 5px 8px; cursor: pointer; font-size: 11px;
    }
    .new-form button:disabled { opacity: 0.4; cursor: default; }
    .icon-field { max-width: 70px; }

    .error-msg { color: #fb7185; font-size: 11px; padding: 4px 8px; }
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

  private promptDebounce: ReturnType<typeof setTimeout> | null = null;

  ngOnInit(): void {
    this.svc.load();
  }

  startEdit(s: ChatSession): void {
    this.editingId = s.id;
    this.editName = s.name;
    this.expandedId = '';
  }

  saveEdit(s: ChatSession): void {
    const name = this.editName.trim();
    if (name && name !== s.name) {
      this.svc.update(s.id, { name });
    }
    this.editingId = '';
  }

  cancelEdit(): void {
    this.editingId = '';
  }

  toggleExpand(): void {
    this.showNew = !this.showNew;
  }

  createNew(): void {
    const name = this.newName.trim();
    if (!name) return;
    this.svc.create({ name, icon: this.newIcon || '💬', system_prompt: this.newPrompt });
    this.newName = '';
    this.newIcon = '💬';
    this.newPrompt = '';
    this.showNew = false;
  }

  confirmDelete(s: ChatSession): void {
    if (confirm(`Session "${s.name}" wirklich löschen?`)) {
      this.svc.remove(s.id);
    }
  }

  getSettingStr(s: ChatSession, key: string, fallback: string): string {
    return String(s.settings?.[key] ?? fallback);
  }

  getSettingBool(s: ChatSession, key: string): boolean {
    return !!s.settings?.[key];
  }

  patchSetting(s: ChatSession, key: string, value: unknown): void {
    this.svc.update(s.id, { settings: { ...s.settings, [key]: value } });
  }

  patchPrompt(s: ChatSession, value: string): void {
    if (this.promptDebounce) clearTimeout(this.promptDebounce);
    this.promptDebounce = setTimeout(() => {
      this.svc.update(s.id, { system_prompt: value });
    }, 600);
  }
}
