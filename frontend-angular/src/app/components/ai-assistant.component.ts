import { Component, OnInit, ViewChild, ElementRef, AfterViewChecked } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  requiresConfirmation?: boolean;
  toolCalls?: any[];
  pendingPrompt?: string;
  sgptCommand?: string;
}

@Component({
  standalone: true,
  selector: 'app-ai-assistant',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="ai-assistant-container" [class.minimized]="minimized">
      <div class="header" (click)="toggleMinimize()">
        <span>✨ KI Assistent</span>
        <div class="controls">
          <button (click)="toggleMinimize(); $event.stopPropagation()" class="control-btn">
            {{ minimized ? '▲' : '▼' }}
          </button>
        </div>
      </div>
      
      <div class="content" *ngIf="!minimized">
        <div #chatBox class="chat-history">
          <div *ngFor="let msg of chatHistory" [style.text-align]="msg.role === 'user' ? 'right' : 'left'" style="margin-bottom: 10px;">
            <div class="msg-bubble" 
                 [class.user-msg]="msg.role === 'user'"
                 [class.assistant-msg]="msg.role === 'assistant'">
              <div [innerHTML]="renderMarkdown(msg.content)"></div>
              
              <div *ngIf="msg.sgptCommand" style="margin-top: 10px; border-top: 1px solid var(--border); padding-top: 8px;">
                <div style="font-size: 12px; margin-bottom: 4px;">
                  💻 <strong>Shell-Befehl ausführen:</strong>
                  <pre style="background: rgba(0,0,0,0.2); padding: 5px; border-radius: 4px; overflow-x: auto;">{{msg.sgptCommand}}</pre>
                </div>
                <div style="display: flex; gap: 5px; margin-top: 8px;">
                  <button (click)="executeSgpt(msg)" class="confirm-btn">Ausführen</button>
                  <button (click)="msg.sgptCommand = undefined" class="cancel-btn">Ignorieren</button>
                </div>
              </div>

              <div *ngIf="msg.requiresConfirmation" style="margin-top: 10px; border-top: 1px solid var(--border); padding-top: 8px;">
                <div *ngFor="let tc of msg.toolCalls" style="font-size: 12px; margin-bottom: 4px;">
                  🛠️ <strong>{{tc.name}}</strong> ({{tc.args | json}})
                </div>
                <div style="display: flex; gap: 5px; margin-top: 8px;">
                  <button (click)="confirmAction(msg)" class="confirm-btn">Ausführen</button>
                  <button (click)="cancelAction(msg)" class="cancel-btn">Abbrechen</button>
                </div>
              </div>
            </div>
          </div>
          <div *ngIf="busy" class="muted" style="font-size: 12px;">KI denkt nach...</div>
        </div>
        
        <div class="input-area">
          <input [(ngModel)]="chatInput" (keyup.enter)="sendChat()" placeholder="Frage mich etwas..." [disabled]="busy">
          <button (click)="sendChat()" [disabled]="busy || !chatInput.trim()">Senden</button>
        </div>
        <div class="muted" style="font-size: 11px; margin-top: 6px;">
          Hinweis: Aktionen erfordern Adminrechte und Bestaetigung.
        </div>
      </div>
    </div>

    <style>
      .ai-assistant-container {
        position: fixed;
        bottom: 0;
        right: 20px;
        width: 380px;
        background: var(--card-bg);
        border: 1px solid var(--border);
        border-radius: 8px 8px 0 0;
        box-shadow: 0 -2px 10px rgba(0,0,0,0.1);
        z-index: 1000;
        display: flex;
        flex-direction: column;
        transition: height 0.3s ease;
        color: var(--fg);
      }
      .ai-assistant-container.minimized {
        height: 40px;
      }
      .header {
        background: var(--accent);
        color: white;
        padding: 8px 15px;
        border-radius: 8px 8px 0 0;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-weight: bold;
      }
      .content {
        height: 450px;
        display: flex;
        flex-direction: column;
        padding: 10px;
      }
      .chat-history {
        flex-grow: 1;
        overflow-y: auto;
        margin-bottom: 10px;
        padding-right: 5px;
      }
      .msg-bubble {
        display: inline-block;
        padding: 8px 12px;
        border-radius: 15px;
        max-width: 90%;
        font-size: 14px;
        line-height: 1.4;
        text-align: left;
        white-space: pre-wrap;
      }
      .user-msg {
        background: var(--accent);
        color: white;
        border-bottom-right-radius: 2px;
      }
      .assistant-msg {
        background: var(--bg);
        color: var(--fg);
        border: 1px solid var(--border);
        border-bottom-left-radius: 2px;
      }
      .assistant-msg pre {
        background: #1e1e1e;
        color: #d4d4d4;
        padding: 8px;
        border-radius: 4px;
        overflow-x: auto;
        font-family: monospace;
        margin: 5px 0;
      }
      .assistant-msg code {
        background: rgba(0,0,0,0.05);
        padding: 2px 4px;
        border-radius: 3px;
        font-family: monospace;
      }
      .input-area {
        display: flex;
        gap: 5px;
      }
      .input-area input {
        flex-grow: 1;
        background: var(--input-bg);
        color: var(--fg);
        border: 1px solid var(--border);
      }
      .control-btn {
        background: none;
        border: none;
        color: white;
        cursor: pointer;
        font-size: 12px;
      }
      .confirm-btn {
        background: #28a745;
        color: white;
        border: none;
        padding: 4px 8px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 12px;
      }
      .cancel-btn {
        background: #dc3545;
        color: white;
        border: none;
        padding: 4px 8px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 12px;
      }
    </style>
  `
})
export class AiAssistantComponent implements OnInit, AfterViewChecked {
  @ViewChild('chatBox') private chatBox?: ElementRef;

  minimized = true;
  busy = false;
  chatInput = '';
  chatHistory: ChatMessage[] = [];
  
  hub = this.dir.list().find(a => a.role === 'hub');

  constructor(
    private dir: AgentDirectoryService,
    private agentApi: AgentApiService,
    private ns: NotificationService
  ) {}

  ngOnInit() {
    this.chatHistory.push({ role: 'assistant', content: 'Hallo! Ich bin dein globaler KI-Assistent. Wie kann ich dir heute helfen?' });
  }

  ngAfterViewChecked() {
    this.scrollToBottom();
  }

  toggleMinimize() {
    this.minimized = !this.minimized;
  }

  sendChat() {
    if (!this.chatInput.trim()) return;

    const hub = this.hub;
    if (!hub) {
      this.ns.info('Hub agent is not configured.');
      return;
    }
    
    const userMsg = this.chatInput;
    const history = this.buildHistoryPayload();
    
    this.chatHistory.push({ role: 'user', content: userMsg });
    this.chatInput = '';
    this.busy = true;
    const assistantMsg: ChatMessage = { role: 'assistant', content: '' };
    this.chatHistory.push(assistantMsg);

    this.streamChat(userMsg, history, assistantMsg).catch(() => {
      assistantMsg.content = '';
      this.agentApi.llmGenerate(hub.url, userMsg, null, undefined, { history }).subscribe({
        next: r => {
          const responseText = typeof r?.response === 'string' ? r.response : '';
          if (r?.requires_confirmation && Array.isArray(r.tool_calls)) {
            assistantMsg.content = responseText && responseText.trim() ? responseText : 'Pending actions require confirmation.';
            assistantMsg.requiresConfirmation = true;
            assistantMsg.toolCalls = r.tool_calls;
            assistantMsg.pendingPrompt = userMsg;
          } else if (!responseText || !responseText.trim()) {
            this.ns.error('Leere LLM-Antwort');
            assistantMsg.content = '';
          } else {
            assistantMsg.content = responseText;
            this.checkForSgptCommand(assistantMsg);
          }
        },
        error: () => { 
          this.ns.error('KI-Chat fehlgeschlagen'); 
          this.busy = false;
        },
        complete: () => { this.busy = false; }
      });
    });
  }

  confirmAction(msg: { toolCalls?: any[]; pendingPrompt?: string; requiresConfirmation?: boolean }) {
    const hub = this.hub;
    if (!hub || !msg.toolCalls || msg.toolCalls.length === 0) return;
    const prompt = msg.pendingPrompt || '';
    const history = this.buildHistoryPayload();
    const toolCalls = msg.toolCalls;
    this.busy = true;

    msg.requiresConfirmation = false;
    msg.toolCalls = [];

    this.agentApi.llmGenerate(hub.url, prompt, null, undefined, {
      history,
      tool_calls: toolCalls,
      confirm_tool_calls: true
    }).subscribe({
      next: r => {
        this.chatHistory.push({ role: 'assistant', content: r.response || 'Actions completed.' });
      },
      error: () => { 
        this.ns.error('Tool execution failed'); 
        this.busy = false;
      },
      complete: () => { this.busy = false; }
    });
  }

  cancelAction(msg: { toolCalls?: any[]; requiresConfirmation?: boolean }) {
    msg.requiresConfirmation = false;
    msg.toolCalls = [];
    this.chatHistory.push({ role: 'assistant', content: 'Pending actions cancelled.' });
  }

  executeSgpt(msg: ChatMessage) {
    const hub = this.hub;
    if (!hub || !msg.sgptCommand) return;
    
    const cmd = msg.sgptCommand;
    msg.sgptCommand = undefined;
    this.busy = true;

    this.agentApi.sgptExecute(hub.url, cmd, ['--shell']).subscribe({
      next: r => {
        let resultMsg = '### Shell Output\n';
        if (r.output) resultMsg += '```text\n' + r.output + '\n```';
        if (r.errors) resultMsg += '\n### Errors\n```text\n' + r.errors + '\n```';
        if (!r.output && !r.errors) resultMsg = 'Befehl ohne Ausgabe ausgeführt.';
        
        this.chatHistory.push({ role: 'assistant', content: resultMsg });
      },
      error: (err) => {
        this.ns.error('SGPT Ausführung fehlgeschlagen');
        this.chatHistory.push({ role: 'assistant', content: 'Fehler: ' + (err.error?.error || err.message) });
        this.busy = false;
      },
      complete: () => { this.busy = false; }
    });
  }

  private checkForSgptCommand(msg: ChatMessage) {
    // Sucht nach Mustern wie `sgpt --shell "..."` oder einfach Code-Blöcken die wie Befehle aussehen
    // Eine einfache Heuristik: Wenn die KI einen Befehl vorschlägt
    const shellMatch = msg.content.match(/```(?:bash|sh|shell)?\n([\s\S]+?)\n```/);
    if (shellMatch && shellMatch[1]) {
      const potentialCmd = shellMatch[1].trim();
      // Nur wenn es einzeilig ist oder wir sicher sind, dass es ein Befehl ist
      if (potentialCmd.length > 0 && potentialCmd.length < 200 && !potentialCmd.includes('\n')) {
        msg.sgptCommand = potentialCmd;
      }
    }
  }

  renderMarkdown(text: string): string {
    if (!text) return '';
    const rendered = marked.parse(text, { breaks: true });
    const html = typeof rendered === 'string' ? rendered : '';
    return DOMPurify.sanitize(html);
  }

  private scrollToBottom(): void {
    if (this.chatBox) {
      this.chatBox.nativeElement.scrollTop = this.chatBox.nativeElement.scrollHeight;
    }
  }

  private buildHistoryPayload(): Array<{ role: string; content: string }> {
    const maxItems = 10;
    const history = this.chatHistory.slice(-maxItems);
    return history.map(m => ({ role: m.role, content: m.content }));
  }

  private async streamChat(
    prompt: string,
    history: Array<{ role: string; content: string }>,
    assistantMsg: { content: string; requiresConfirmation?: boolean; toolCalls?: any[]; pendingPrompt?: string }
  ): Promise<void> {
    if (!this.hub) throw new Error('missing hub');
    const hub = this.hub;
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (hub.token) headers['Authorization'] = `Bearer ${hub.token}`;

    const res = await fetch(`${hub.url}/llm/generate`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ prompt, history, stream: true })
    });
    if (!res.ok || !res.body) throw new Error('stream failed');

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx = buffer.indexOf('\n\n');
      while (idx !== -1) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const lines = chunk.split('\n');
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          const data = line.replace(/^data:\s?/, '');
          if (data === '[DONE]') {
            this.busy = false;
            return;
          }
          assistantMsg.content += data;
        }
        idx = buffer.indexOf('\n\n');
      }
    }
    this.busy = false;
  }
}


