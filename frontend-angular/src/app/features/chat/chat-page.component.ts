import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule, AsyncPipe, DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { ChatSessionsService, ChatSession, CreateSessionPayload } from '../../services/chat-sessions.service';
import { ChatHistoryService, ChatHistoryMessage } from '../../services/chat-history.service';
import { AiSnakeChatService } from '../../services/ai-snake-chat.service';
import { AiSnakeTraceViewerComponent } from '../../components/ai-snake-trace-viewer.component';
import { AiSnakeTraceService } from '../../services/ai-snake-trace.service';

@Component({
  selector: 'app-chat-page',
  standalone: true,
  imports: [CommonModule, AsyncPipe, DatePipe, FormsModule, AiSnakeTraceViewerComponent],
  template: `
<div class="chat-page">

  <!-- ══ 1: Session-Sidebar ══ -->
  <aside class="sidebar">
    <div class="sidebar-head">
      <span class="sidebar-title">AI Chats</span>
      <button class="new-btn" (click)="showNew = !showNew" title="Neue Session">＋</button>
    </div>

    <div class="session-list">
      @for (s of (svc.sessions$ | async) || []; track s.id) {
        <button class="sess-item"
                [class.active]="s.id === selectedId"
                [class.is-current]="s.id === (svc.activeSessionId$ | async)"
                (click)="select(s)">
          <span class="si-icon">{{ s.icon || '💬' }}</span>
          <span class="si-body">
            <span class="si-name">{{ s.name }}</span>
            <span class="si-meta">{{ lastMessagePreview(s.id) }}</span>
          </span>
          @if (s.id === (svc.activeSessionId$ | async)) {
            <span class="si-active-dot" title="Aktiv">●</span>
          }
        </button>
      }
    </div>

    @if (showNew) {
      <div class="new-form">
        <div class="nf-row">
          <input [(ngModel)]="newIcon" maxlength="4" class="nf-icon" placeholder="💬" />
          <input [(ngModel)]="newName" class="nf-name" placeholder="Name *"
                 (keydown.enter)="createSession()" />
        </div>
        <label class="nf-label">Backend
          <select [(ngModel)]="newBackend">
            <option value="ananta-worker">ananta-worker</option>
            <option value="opencode">opencode</option>
            <option value="lmstudio">lmstudio</option>
            <option value="hermes">hermes</option>
          </select>
        </label>
        <label class="nf-label inline">
          <input type="checkbox" [(ngModel)]="newCC" /> CodeCompass
        </label>
        <label class="nf-label">System-Prompt
          <textarea rows="2" [(ngModel)]="newPrompt" placeholder="optional…"></textarea>
        </label>
        <div class="nf-actions">
          <button class="btn-primary" (click)="createSession()" [disabled]="!newName.trim()">Anlegen</button>
          <button class="btn-ghost" (click)="showNew = false">Abbrechen</button>
        </div>
      </div>
    }
  </aside>

  <!-- ══ 2: Nachrichten / Einstellungen ══ -->
  <main class="main">
    @if (!selected) {
      <div class="empty">
        <div class="empty-icon">💬</div>
        <div>Session wählen oder neu anlegen.</div>
      </div>
    } @else {

      <!-- Session-Header -->
      <div class="sess-header">
        <div class="sh-left">
          <span class="sh-icon">{{ selected.icon || '💬' }}</span>
          <div class="sh-title-block">
            @if (editingName) {
              <input class="name-edit" [(ngModel)]="editNameVal"
                     (keydown.enter)="saveName()" (keydown.escape)="editingName = false" />
              <button class="icon-btn" (click)="saveName()">✓</button>
              <button class="icon-btn" (click)="editingName = false">✕</button>
            } @else {
              <span class="sh-name">{{ selected.name }}</span>
              <button class="icon-btn dim" (click)="startEditName()" title="Umbenennen">✎</button>
            }
            <span class="sh-meta">{{ sessBackend(selected) }}
              @if (sessCC(selected)) { <span class="cc-tag">CC</span> }
            </span>
          </div>
        </div>
        <div class="sh-actions">
          @if (selected.id !== (svc.activeSessionId$ | async)) {
            <button class="btn-activate" (click)="activateSelected()">▶ Aktivieren</button>
          } @else {
            <span class="active-badge">● Aktiv</span>
          }
          <button class="icon-btn danger" (click)="deleteSelected()"
                  [disabled]="((svc.sessions$ | async) || []).length <= 1"
                  title="Löschen">✕</button>
        </div>
      </div>

      <!-- Tabs -->
      <div class="tabs">
        <button [class.tab-active]="detailTab === 'messages'" (click)="detailTab = 'messages'">
          Nachrichten ({{ messageCount() }})
        </button>
        <button [class.tab-active]="detailTab === 'settings'" (click)="detailTab = 'settings'">
          Einstellungen
        </button>
      </div>

      <!-- Nachrichten -->
      @if (detailTab === 'messages') {
        <div class="messages-area">
          @if (messages().length === 0) {
            <div class="no-msgs">
              <div>Keine Nachrichten.</div>
              <div class="hint">Aktiviere die Session (▶) und sende eine Nachricht im Chat-Panel.</div>
            </div>
          } @else {
            <div class="msg-list">
              @for (m of messages(); track m.id) {
                <div class="msg-row" [class.msg-ai]="m.isAI" [class.msg-user]="!m.isAI">
                  <div class="msg-header">
                    <span class="msg-sender">{{ m.isAI ? '🤖' : '👤' }} {{ m.senderId }}</span>
                    <span class="msg-ts">{{ m.ts | date:'HH:mm:ss' }}</span>
                  </div>
                  <div class="msg-text">{{ m.text }}</div>
                </div>
              }
            </div>
            <div class="msg-footer">
              <button class="btn-ghost small" (click)="clearMessages()">Verlauf löschen</button>
            </div>
          }
        </div>
      }

      <!-- Einstellungen -->
      @if (detailTab === 'settings') {
        <div class="settings-area">
          <label class="sl">Backend
            <select [ngModel]="sessBackend(selected)"
                    (ngModelChange)="patchSetting('chat_backend', $event)">
              <option value="ananta-worker">ananta-worker</option>
              <option value="opencode">opencode</option>
              <option value="lmstudio">lmstudio</option>
              <option value="hermes">hermes</option>
            </select>
          </label>
          <label class="sl">Retrieval-Profil
            <select [ngModel]="getSetting('chat_retrieval_profile', 'auto')"
                    (ngModelChange)="patchSetting('chat_retrieval_profile', $event)">
              <option value="auto">auto</option>
              <option value="code_first">code_first</option>
              <option value="none">none</option>
            </select>
          </label>
          <label class="sl inline">
            <input type="checkbox" [ngModel]="sessCC(selected)"
                   (ngModelChange)="patchSetting('chat_use_codecompass', $event)" />
            CodeCompass aktiv
          </label>
          <label class="sl inline">
            <input type="checkbox"
                   [ngModel]="getSettingBool('chat_code_questions_repo_first')"
                   (ngModelChange)="patchSetting('chat_code_questions_repo_first', $event)" />
            Code-Fragen: Repo bevorzugen
          </label>
          <label class="sl">System-Prompt
            <textarea rows="6" [ngModel]="selected.system_prompt"
                      (ngModelChange)="patchPromptDebounced($event)"
                      placeholder="Leer = System-Standard"></textarea>
          </label>
          <div class="meta-block">
            <span>ID: <code>{{ selected.id }}</code></span>
            @if (selected.created_at) {
              <span>Erstellt: {{ selected.created_at * 1000 | date:'dd.MM.yy HH:mm' }}</span>
            }
          </div>
        </div>
      }
    }
  </main>

  <!-- ══ 3: Trace-Panel (immer sichtbar) ══ -->
  <section class="trace-panel">
    <div class="tp-head">
      <span class="tp-title">Ablauf-Trace</span>
      <span class="tp-sub">Was Ananta gerade tut — Dateien · Prompt · LLM</span>
      @if (traceSvc.traceStatus$ | async; as st) {
        <span class="tp-badge" [class]="'tbadge-' + st">{{ st }}</span>
      }
    </div>
    <div class="tp-body">
      <app-ai-snake-trace-viewer />
    </div>
    @if (!(traceSvc.activeTraceId$ | async)) {
      <div class="tp-hint">
        Schick eine Nachricht im Chat-Panel — der Ablauf erscheint hier in Echtzeit.
      </div>
    }
  </section>

</div>
  `,
  styles: [`
    :host { display: block; height: 100%; }

    .chat-page {
      display: grid;
      grid-template-columns: 220px 1fr 440px;
      height: calc(100vh - 120px);
      min-height: 400px;
      border: 1px solid #1a2d4a;
      border-radius: 6px;
      overflow: hidden;
      background: #0b1220;
      color: #c8d8f8;
      font-family: inherit;
    }

    /* ── Sidebar ── */
    .sidebar {
      border-right: 1px solid #1a2d4a;
      display: flex; flex-direction: column;
      background: #09172a; overflow: hidden;
    }
    .sidebar-head {
      display: flex; justify-content: space-between; align-items: center;
      padding: 10px 12px; border-bottom: 1px solid #1a2d4a;
      background: #0d1e34; flex-shrink: 0;
    }
    .sidebar-title { font-size: 13px; font-weight: 600; color: #c8d8f8; }
    .new-btn {
      background: transparent; border: 1px solid #2a4070; color: #7fffd4;
      padding: 2px 8px; cursor: pointer; font-size: 16px; border-radius: 3px;
    }
    .new-btn:hover { background: #102238; }
    .session-list { flex: 1; overflow-y: auto; }
    .sess-item {
      width: 100%; display: flex; align-items: center; gap: 8px;
      padding: 8px 10px; border: none; background: transparent;
      color: #8ab0d8; cursor: pointer; text-align: left; border-bottom: 1px solid #152040;
    }
    .sess-item:hover { background: #0d1e34; color: #c8d8f8; }
    .sess-item.active { background: #0e2540; color: #c8d8f8; }
    .sess-item.is-current { border-left: 2px solid #7fffd4; }
    .si-icon { font-size: 16px; flex-shrink: 0; }
    .si-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
    .si-name { font-size: 12px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .si-meta { font-size: 10px; color: #4a6a9a; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .si-active-dot { color: #7fffd4; font-size: 8px; flex-shrink: 0; }

    .new-form {
      padding: 10px; border-top: 1px solid #1a2d4a; background: #08131f;
      flex-shrink: 0; display: flex; flex-direction: column; gap: 7px;
    }
    .nf-row { display: flex; gap: 6px; }
    .nf-icon { width: 38px; background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8; padding: 4px; font-size: 13px; text-align: center; border-radius: 2px; }
    .nf-name { flex: 1; background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8; padding: 4px 6px; font-size: 12px; font-family: inherit; border-radius: 2px; }
    .nf-label { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: #6b8ab8; }
    .nf-label.inline { flex-direction: row; align-items: center; gap: 6px; color: #c8d8f8; }
    .nf-label select, .nf-label textarea { background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8; padding: 3px 5px; font-size: 11px; font-family: inherit; border-radius: 2px; }
    .nf-label textarea { resize: vertical; }
    .nf-actions { display: flex; gap: 6px; }

    /* ── Main ── */
    .main { display: flex; flex-direction: column; overflow: hidden; border-right: 1px solid #1a2d4a; }
    .empty {
      flex: 1; display: flex; flex-direction: column; align-items: center;
      justify-content: center; gap: 10px; color: #3a5a8a; font-size: 13px;
    }
    .empty-icon { font-size: 36px; }

    .sess-header {
      display: flex; justify-content: space-between; align-items: center;
      padding: 10px 14px; border-bottom: 1px solid #1a2d4a;
      background: #0d1e34; flex-shrink: 0;
    }
    .sh-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .sh-icon { font-size: 22px; flex-shrink: 0; }
    .sh-title-block { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
    .sh-name { font-size: 14px; font-weight: 600; color: #e0f0ff; }
    .sh-meta { font-size: 11px; color: #4a7aaa; display: flex; align-items: center; gap: 5px; }
    .cc-tag { background: #0a2a1a; border: 1px solid #1a5a30; color: #3affaa; padding: 1px 4px; font-size: 9px; border-radius: 2px; }
    .name-edit { background: #0f1c30; border: 1px solid #2a4070; color: #c8d8f8; padding: 3px 6px; font-size: 13px; font-family: inherit; border-radius: 2px; }
    .sh-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
    .active-badge { color: #7fffd4; font-size: 11px; }

    .tabs {
      display: flex; border-bottom: 1px solid #1a2d4a; flex-shrink: 0; background: #09172a;
    }
    .tabs button {
      padding: 7px 16px; border: none; background: transparent; color: #4a6a9a;
      cursor: pointer; font-size: 12px; border-bottom: 2px solid transparent;
      font-family: inherit;
    }
    .tabs button:hover { color: #c8d8f8; }
    .tabs button.tab-active { color: #7fffd4; border-bottom-color: #7fffd4; }

    .messages-area { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
    .no-msgs {
      flex: 1; display: flex; flex-direction: column; align-items: center;
      justify-content: center; gap: 10px; color: #3a5a8a; font-size: 13px; padding: 20px; text-align: center;
    }
    .hint { font-size: 11px; color: #2a4a6a; line-height: 1.5; }
    .msg-list { flex: 1; overflow-y: auto; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
    .msg-row {
      display: flex; flex-direction: column; gap: 4px;
      padding: 8px 12px; border-radius: 5px; border: 1px solid #152040;
    }
    .msg-user { background: #0a1830; border-color: #1a2d4a; }
    .msg-ai   { background: #0a1c14; border-color: #1a3a25; }
    .msg-header { display: flex; justify-content: space-between; align-items: center; }
    .msg-sender { font-size: 11px; color: #4a6a9a; font-weight: 500; }
    .msg-ts     { font-size: 10px; color: #2a4a6a; }
    .msg-text   { font-size: 12px; color: #c8d8f8; white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
    .msg-footer { padding: 6px 14px; border-top: 1px solid #152040; flex-shrink: 0; }

    .settings-area {
      flex: 1; overflow-y: auto; padding: 14px 16px;
      display: flex; flex-direction: column; gap: 12px;
    }
    .sl { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #6b8ab8; }
    .sl.inline { flex-direction: row; align-items: center; gap: 8px; color: #c8d8f8; }
    .sl select, .sl textarea {
      background: #0f1c30; border: 1px solid #1a2d4a; color: #c8d8f8;
      padding: 5px 7px; font-size: 12px; font-family: inherit; border-radius: 3px;
    }
    .sl textarea { resize: vertical; }
    .meta-block {
      display: flex; gap: 16px; font-size: 11px; color: #3a5a8a; margin-top: 4px;
      padding-top: 10px; border-top: 1px solid #152040;
    }
    .meta-block code { color: #6b9abf; background: #0a1830; padding: 1px 4px; border-radius: 2px; }

    /* ══ Trace Panel ══ */
    .trace-panel {
      display: flex; flex-direction: column; overflow: hidden;
      background: #080f1a;
    }
    .tp-head {
      display: flex; align-items: center; gap: 8px; flex-shrink: 0;
      padding: 9px 14px; background: #091525; border-bottom: 1px solid #152040;
    }
    .tp-title { font-size: 13px; font-weight: 600; color: #4a9acc; }
    .tp-sub   { font-size: 10px; color: #2a4a6a; flex: 1; }
    .tp-badge { font-size: 9px; padding: 2px 6px; border-radius: 8px; border: 1px solid #1a3a5a; }
    .tbadge-running   { color: #7fffd4; border-color: #1a5a3a; background: #061810; animation: tp-pulse 1.2s infinite; }
    .tbadge-completed { color: #3acc88; border-color: #1a4a2a; }
    .tbadge-failed    { color: #fb7185; border-color: #4a1a1a; }
    .tbadge-idle, .tbadge-unknown { color: #2a4060; border-color: #1a2030; }
    @keyframes tp-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

    .tp-body {
      flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden;
    }
    .tp-hint {
      flex-shrink: 0; padding: 8px 14px;
      font-size: 11px; color: #1e3558; border-top: 1px solid #0e1e30; text-align: center;
    }

    /* ── Buttons ── */
    button { font-family: inherit; cursor: pointer; }
    .btn-primary { background: #102238; border: 1px solid #2a5090; color: #7fffd4; padding: 5px 10px; font-size: 12px; border-radius: 3px; }
    .btn-primary:disabled { opacity: 0.35; cursor: default; }
    .btn-primary:not(:disabled):hover { background: #183250; }
    .btn-ghost  { background: transparent; border: 1px solid #1a2d4a; color: #6b8ab8; padding: 5px 10px; font-size: 12px; border-radius: 3px; }
    .btn-ghost:hover { color: #c8d8f8; }
    .btn-ghost.small { padding: 3px 8px; font-size: 11px; }
    .btn-activate { background: #0a2a18; border: 1px solid #1a5030; color: #3affaa; padding: 4px 10px; font-size: 12px; border-radius: 3px; }
    .btn-activate:hover { background: #123a22; }
    .icon-btn { background: transparent; border: none; color: #4a6a9a; padding: 2px 5px; font-size: 12px; border-radius: 2px; cursor: pointer; }
    .icon-btn:hover:not(:disabled) { color: #c8d8f8; }
    .icon-btn.dim { color: #2a4a6a; }
    .icon-btn.danger:hover:not(:disabled) { color: #fb7185; }
    .icon-btn:disabled { opacity: 0.25; cursor: default; }
  `],
})
export class ChatPageComponent implements OnInit, OnDestroy {
  readonly svc = inject(ChatSessionsService);
  readonly history = inject(ChatHistoryService);
  readonly traceSvc = inject(AiSnakeTraceService);
  private snakeSvc = inject(AiSnakeChatService);

  selected: ChatSession | null = null;
  selectedId = '';
  detailTab: 'messages' | 'settings' = 'messages';

  showNew = false;
  newName = '';
  newIcon = '💬';
  newPrompt = '';
  newBackend = 'ananta-worker';
  newCC = true;

  editingName = false;
  editNameVal = '';

  private histSub?: Subscription;
  private promptDebounce: ReturnType<typeof setTimeout> | null = null;

  ngOnInit(): void {
    this.svc.load();
    this.histSub = this.history.updated$.subscribe(() => {});
    this.svc.sessions$.subscribe(sessions => {
      if (!this.selected && sessions.length > 0) {
        const activeId = this.svc.activeSessionId$.value;
        const active = sessions.find(s => s.id === activeId) ?? sessions[0];
        this.select(active);
      }
      if (this.selected) {
        const updated = sessions.find(s => s.id === this.selected!.id);
        if (updated) this.selected = updated;
      }
    });
  }

  ngOnDestroy(): void {
    this.histSub?.unsubscribe();
  }

  select(s: ChatSession): void {
    this.selected = s;
    this.selectedId = s.id;
    this.editingName = false;
    this.detailTab = 'messages';
  }

  activateSelected(): void {
    if (this.selected) this.svc.activate(this.selected.id);
  }

  deleteSelected(): void {
    if (!this.selected) return;
    if (!confirm(`Session "${this.selected.name}" wirklich löschen?`)) return;
    const sessions = this.svc.sessions$.value;
    const next = sessions.find(s => s.id !== this.selected!.id);
    this.svc.remove(this.selected.id);
    this.selected = next ?? null;
    this.selectedId = next?.id ?? '';
  }

  createSession(): void {
    const name = this.newName.trim();
    if (!name) return;
    const payload: CreateSessionPayload = {
      name, icon: this.newIcon || '💬',
      system_prompt: this.newPrompt,
      settings: { chat_backend: this.newBackend, chat_use_codecompass: this.newCC },
    };
    this.svc.create(payload);
    this.newName = ''; this.newIcon = '💬'; this.newPrompt = '';
    this.newBackend = 'ananta-worker'; this.newCC = true;
    this.showNew = false;
  }

  startEditName(): void {
    if (!this.selected) return;
    this.editNameVal = this.selected.name;
    this.editingName = true;
  }

  saveName(): void {
    const name = this.editNameVal.trim();
    if (name && this.selected && name !== this.selected.name) {
      this.svc.update(this.selected.id, { name });
    }
    this.editingName = false;
  }

  messages(): ChatHistoryMessage[] {
    return this.history.getMessages(this.selectedId);
  }

  messageCount(): number {
    return this.history.getMessages(this.selectedId).length;
  }

  clearMessages(): void {
    if (this.selectedId) this.history.clearSession(this.selectedId);
  }

  lastMessagePreview(sessionId: string): string {
    const msgs = this.history.getMessages(sessionId);
    if (!msgs.length) return 'Keine Nachrichten';
    const last = msgs[msgs.length - 1];
    const text = last.text?.substring(0, 40) ?? '';
    return (last.isAI ? '🤖 ' : '👤 ') + text + (last.text?.length > 40 ? '…' : '');
  }

  sessBackend(s: ChatSession): string {
    return String(s.settings?.['chat_backend'] ?? '');
  }

  sessCC(s: ChatSession): boolean {
    return !!s.settings?.['chat_use_codecompass'];
  }

  getSetting(key: string, fallback: string): string {
    return String(this.selected?.settings?.[key] ?? fallback);
  }

  getSettingBool(key: string): boolean {
    return !!this.selected?.settings?.[key];
  }

  patchSetting(key: string, value: unknown): void {
    if (!this.selected) return;
    this.svc.update(this.selected.id, { settings: { ...this.selected.settings, [key]: value } });
  }

  patchPromptDebounced(value: string): void {
    if (this.promptDebounce) clearTimeout(this.promptDebounce);
    this.promptDebounce = setTimeout(() => {
      if (this.selected) this.svc.update(this.selected.id, { system_prompt: value });
    }, 600);
  }
}
