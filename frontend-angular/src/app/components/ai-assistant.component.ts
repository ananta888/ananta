import { AfterViewChecked, ChangeDetectorRef, Component, ElementRef, NgZone, OnInit, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { NotificationService } from '../services/notification.service';

interface ContextMeta {
  policy_version?: string;
  chunk_count?: number;
  token_estimate?: number;
  strategy?: any;
}

interface ContextSource {
  engine: string;
  source: string;
  score?: number;
  preview?: string;
  previewLoading?: boolean;
  previewError?: string;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  requiresConfirmation?: boolean;
  toolCalls?: any[];
  pendingPrompt?: string;
  sgptCommand?: string;
  cliBackendUsed?: string;
  contextMeta?: ContextMeta;
  contextSources?: ContextSource[];
}

type CliBackend = 'auto' | 'sgpt' | 'opencode' | 'aider' | 'mistral_code';

@Component({
  standalone: true,
  selector: 'app-ai-assistant',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="ai-assistant-container" [class.minimized]="minimized">
      <div class="header" (click)="toggleMinimize()">
        <span>AI Assistant</span>
        <div class="controls">
          <button (click)="toggleMinimize(); $event.stopPropagation()" class="control-btn">
            {{ minimized ? '^' : 'v' }}
          </button>
        </div>
      </div>

      <div class="content" *ngIf="!minimized">
        <div #chatBox class="chat-history">
          <div *ngFor="let msg of chatHistory" [style.text-align]="msg.role === 'user' ? 'right' : 'left'" style="margin-bottom: 10px;">
            <div class="msg-bubble" [class.user-msg]="msg.role === 'user'" [class.assistant-msg]="msg.role === 'assistant'">
              <div [innerHTML]="renderMarkdown(msg.content)"></div>
              <div *ngIf="msg.cliBackendUsed" class="muted" style="font-size: 11px; margin-top: 4px;">
                CLI backend: {{ msg.cliBackendUsed }}
              </div>

              <div *ngIf="msg.contextMeta" class="context-panel">
                <div class="context-title">Context Debug</div>
                <div class="context-meta">
                  policy={{ msg.contextMeta.policy_version || 'v1' }} |
                  chunks={{ msg.contextMeta.chunk_count || 0 }} |
                  tokens={{ msg.contextMeta.token_estimate || 0 }}
                </div>
                <div class="context-meta">strategy={{ msg.contextMeta.strategy | json }}</div>
                <div *ngIf="msg.contextSources?.length" class="context-sources">
                  <div *ngFor="let c of msg.contextSources" class="context-source-row">
                    <div>[{{ c.engine }}] {{ c.source }}</div>
                    <div class="context-actions">
                      <button class="mini-btn" (click)="previewSource(c)">Preview</button>
                      <button class="mini-btn" (click)="copySourcePath(c.source)">Copy Path</button>
                    </div>
                    <pre *ngIf="c.previewLoading" class="source-preview">Loading...</pre>
                    <pre *ngIf="c.previewError" class="source-preview">{{ c.previewError }}</pre>
                    <pre *ngIf="c.preview" class="source-preview">{{ c.preview }}</pre>
                  </div>
                </div>
              </div>

              <div *ngIf="msg.sgptCommand" style="margin-top: 10px; border-top: 1px solid var(--border); padding-top: 8px;">
                <div style="font-size: 12px; margin-bottom: 4px;">
                  <strong>Shell command:</strong>
                  <pre style="background: rgba(0,0,0,0.2); padding: 5px; border-radius: 4px; overflow-x: auto;">{{msg.sgptCommand}}</pre>
                </div>
                <div style="display: flex; gap: 5px; margin-top: 8px;">
                  <button (click)="executeSgpt(msg)" class="confirm-btn">Run</button>
                  <button (click)="msg.sgptCommand = undefined" class="cancel-btn">Ignore</button>
                </div>
              </div>

              <div *ngIf="msg.requiresConfirmation" style="margin-top: 10px; border-top: 1px solid var(--border); padding-top: 8px;">
                <div *ngFor="let tc of msg.toolCalls" style="font-size: 12px; margin-bottom: 4px;">
                  <strong>{{tc.name}}</strong> ({{tc.args | json}})
                </div>
                <div style="display: flex; gap: 5px; margin-top: 8px;">
                  <button (click)="confirmAction(msg)" class="confirm-btn">Run</button>
                  <button (click)="cancelAction(msg)" class="cancel-btn">Cancel</button>
                </div>
              </div>
            </div>
          </div>
          <div *ngIf="busy" class="muted" style="font-size: 12px;">Working...</div>
        </div>

        <div class="input-area">
          <input [(ngModel)]="chatInput" (keyup.enter)="sendChat()" placeholder="Ask me anything..." [disabled]="busy">
          <button (click)="sendChat()" [disabled]="busy || !chatInput.trim()">Send</button>
        </div>
        <label class="hybrid-toggle">
          <input type="checkbox" [(ngModel)]="useHybridContext" [disabled]="busy">
          Hybrid Context (Aider + Vibe + LlamaIndex)
        </label>
        <label class="hybrid-toggle">
          CLI Backend:
          <select [(ngModel)]="cliBackend" [disabled]="busy" (ngModelChange)="onCliBackendChange()">
            <option *ngFor="let backend of availableCliBackends" [value]="backend">{{ backendLabel(backend) }}</option>
          </select>
        </label>
        <div class="muted" style="font-size: 11px; margin-top: 6px;">
          Actions require admin rights and confirmation.
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
      .hybrid-toggle {
        display: block;
        margin-top: 8px;
        font-size: 12px;
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
      .context-panel {
        margin-top: 10px;
        border-top: 1px dashed var(--border);
        padding-top: 8px;
        font-size: 12px;
      }
      .context-title {
        font-weight: 600;
        margin-bottom: 4px;
      }
      .context-meta {
        opacity: 0.9;
      }
      .context-sources {
        margin-top: 5px;
        max-height: 90px;
        overflow-y: auto;
      }
      .context-source-row {
        margin-bottom: 8px;
        padding-bottom: 6px;
        border-bottom: 1px dotted var(--border);
      }
      .context-actions {
        margin-top: 4px;
        display: flex;
        gap: 6px;
      }
      .mini-btn {
        font-size: 11px;
        padding: 2px 6px;
        border: 1px solid var(--border);
        background: transparent;
        color: var(--fg);
        cursor: pointer;
      }
      .source-preview {
        margin-top: 4px;
        max-height: 120px;
        overflow-y: auto;
        background: rgba(0,0,0,0.15);
        padding: 6px;
        border-radius: 4px;
      }
    </style>
  `
})
export class AiAssistantComponent implements OnInit, AfterViewChecked {
  @ViewChild('chatBox') private chatBox?: ElementRef;

  minimized = true;
  busy = false;
  chatInput = '';
  useHybridContext = false;
  cliBackend: CliBackend = 'auto';
  availableCliBackends: CliBackend[] = ['auto', 'sgpt', 'opencode', 'aider', 'mistral_code'];
  chatHistory: ChatMessage[] = [];

  get hub() {
    return this.dir.list().find(a => a.role === 'hub') || this.dir.list()[0];
  }

  constructor(
    private dir: AgentDirectoryService,
    private agentApi: AgentApiService,
    private ns: NotificationService,
    private zone: NgZone,
    private cdr: ChangeDetectorRef
  ) {}

  ngOnInit() {
    this.chatHistory.push({ role: 'assistant', content: 'Hello. I am your AI assistant.' });
    this.loadCliBackend();
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

    if (this.useHybridContext) {
      this.agentApi.sgptExecute(hub.url, userMsg, [], undefined, true, this.cliBackend).subscribe({
        next: r => {
          this.zone.run(() => {
            const output = typeof r?.output === 'string' ? r.output : '';
            assistantMsg.content = output && output.trim() ? output : 'Empty SGPT response';
            if (typeof r?.backend === 'string' && r.backend) {
              assistantMsg.cliBackendUsed = r.backend;
            }
            if (r?.context) {
              assistantMsg.contextMeta = r.context;
            }
            this.agentApi.sgptContext(hub.url, userMsg, undefined, false).subscribe({
              next: ctx => {
                this.zone.run(() => {
                  const chunks = Array.isArray(ctx?.chunks) ? ctx.chunks : [];
                  assistantMsg.contextSources = chunks.map((c: any) => ({
                    engine: c.engine,
                    source: c.source,
                    score: c.score
                  }));
                  this.cdr.detectChanges();
                });
              },
              error: () => {}
            });
            this.checkForSgptCommand(assistantMsg);
            this.cdr.detectChanges();
          });
        },
        error: (e) => {
          this.zone.run(() => {
            this.ns.error('Hybrid SGPT failed');
            assistantMsg.content = 'Error: ' + (e?.error?.message || e?.message || 'Hybrid SGPT failed');
            this.busy = false;
            this.cdr.detectChanges();
          });
        },
        complete: () => { this.zone.run(() => { this.busy = false; this.cdr.detectChanges(); }); }
      });
      return;
    }

    this.agentApi.llmGenerate(hub.url, userMsg, null, undefined, { history }).subscribe({
      next: r => {
        this.zone.run(() => {
          const responseText = typeof r?.response === 'string' ? r.response : '';
          if (r?.requires_confirmation && Array.isArray(r.tool_calls)) {
            assistantMsg.content = responseText && responseText.trim() ? responseText : 'Pending actions require confirmation.';
            assistantMsg.requiresConfirmation = true;
            assistantMsg.toolCalls = r.tool_calls;
            assistantMsg.pendingPrompt = userMsg;
          } else if (!responseText || !responseText.trim()) {
            this.ns.error('Empty LLM response');
            assistantMsg.content = '';
          } else {
            assistantMsg.content = responseText;
            this.checkForSgptCommand(assistantMsg);
          }
          this.cdr.detectChanges();
        });
      },
      error: (e) => {
        this.zone.run(() => {
          const code = e?.error?.error;
          const message = e?.error?.message || e?.message;
          if (code === 'llm_not_configured') {
            this.ns.error('LLM is not configured. Configure it in settings.');
            assistantMsg.content = 'LLM configuration missing.';
          } else {
            this.ns.error('AI chat failed');
            assistantMsg.content = 'Error: ' + (message || 'AI chat failed');
          }
          this.busy = false;
          this.cdr.detectChanges();
        });
      },
      complete: () => { this.zone.run(() => { this.busy = false; this.cdr.detectChanges(); }); }
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

    this.agentApi.execute(hub.url, { command: cmd }).subscribe({
      next: r => {
        let resultMsg = '### Execution Output\n';
        if (r.stdout) resultMsg += '```text\n' + r.stdout + '\n```';
        if (r.stderr) resultMsg += '\n### Errors\n```text\n' + r.stderr + '\n```';
        if (!r.stdout && !r.stderr) resultMsg = 'Command executed without output.';
        this.chatHistory.push({ role: 'assistant', content: resultMsg });
      },
      error: (err) => {
        this.ns.error('Execution failed');
        this.chatHistory.push({ role: 'assistant', content: 'Error: ' + (err.error?.error || err.message) });
        this.busy = false;
      },
      complete: () => { this.busy = false; }
    });
  }

  private checkForSgptCommand(msg: ChatMessage) {
    const shellMatch = msg.content.match(/```(?:bash|sh|shell)?\n([\s\S]+?)\n```/);
    if (shellMatch && shellMatch[1]) {
      const potentialCmd = shellMatch[1].trim();
      if (potentialCmd.length > 0 && potentialCmd.length < 200 && !potentialCmd.includes('\n')) {
        msg.sgptCommand = potentialCmd;
      }
    }
  }

  previewSource(source: ContextSource) {
    const hub = this.hub;
    if (!hub || !source?.source) return;
    source.previewLoading = true;
    source.previewError = undefined;
    source.preview = undefined;
    this.agentApi.sgptSource(hub.url, source.source).subscribe({
      next: r => {
        this.zone.run(() => {
          source.preview = typeof r?.preview === 'string' ? r.preview : 'No preview available';
          this.cdr.detectChanges();
        });
      },
      error: (e) => {
        this.zone.run(() => {
          source.previewError = 'Preview failed: ' + (e?.error?.message || e?.message || 'unknown error');
          this.cdr.detectChanges();
        });
      },
      complete: () => {
        this.zone.run(() => {
          source.previewLoading = false;
          this.cdr.detectChanges();
        });
      }
    });
  }

  async copySourcePath(sourcePath: string) {
    try {
      await navigator.clipboard.writeText(sourcePath);
      this.ns.success('Source path copied');
    } catch {
      this.ns.error('Could not copy source path');
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

  private loadCliBackend() {
    const hub = this.hub;
    if (!hub) return;
    this.agentApi.sgptBackends(hub.url).subscribe({
      next: data => {
        const supported = Object.keys(data?.supported_backends || {});
        const dynamic: CliBackend[] = ['auto'];
        if (supported.includes('sgpt')) dynamic.push('sgpt');
        if (supported.includes('opencode')) dynamic.push('opencode');
        if (supported.includes('aider')) dynamic.push('aider');
        if (supported.includes('mistral_code')) dynamic.push('mistral_code');
        this.availableCliBackends = dynamic;
        if (!this.availableCliBackends.includes(this.cliBackend)) {
          this.cliBackend = 'auto';
        }
        this.cdr.detectChanges();
      },
      error: () => {}
    });
    this.agentApi.getConfig(hub.url).subscribe({
      next: cfg => {
        const value = String(cfg?.sgpt_execution_backend || '').toLowerCase();
        if (
          (value === 'auto' || value === 'sgpt' || value === 'opencode' || value === 'aider' || value === 'mistral_code') &&
          this.availableCliBackends.includes(value as CliBackend)
        ) {
          this.cliBackend = value as CliBackend;
          this.cdr.detectChanges();
        }
      },
      error: () => {}
    });
  }

  onCliBackendChange() {
    const hub = this.hub;
    if (!hub) return;
    this.agentApi.setConfig(hub.url, { sgpt_execution_backend: this.cliBackend }).subscribe({
      next: () => {},
      error: () => {}
    });
  }

  backendLabel(backend: CliBackend): string {
    if (backend === 'sgpt') return 'ShellGPT';
    if (backend === 'opencode') return 'OpenCode';
    if (backend === 'aider') return 'Aider';
    if (backend === 'mistral_code') return 'Mistral Code';
    return 'Auto';
  }
}
