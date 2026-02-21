import { AfterViewChecked, ChangeDetectorRef, Component, ElementRef, NgZone, OnInit, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { NavigationEnd, Router } from '@angular/router';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { filter, forkJoin } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { AiAssistantDomainService } from './ai-assistant-domain.service';
import { AssistantRuntimeContext, ChatMessage, CliBackend, ContextSource } from './ai-assistant.types';

@Component({
  standalone: true,
  selector: 'app-ai-assistant',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="ai-assistant-container" data-testid="assistant-dock" [class.minimized]="minimized" [attr.data-state]="minimized ? 'minimized' : 'expanded'">
      <div class="header" data-testid="assistant-dock-header" (click)="toggleMinimize()">
        <span>AI Assistant</span>
        <div class="controls">
          <button (click)="toggleMinimize(); $event.stopPropagation()" class="control-btn">
            {{ minimized ? '^' : 'v' }}
          </button>
        </div>
      </div>
    
      @if (!minimized) {
        <div class="content">
          <div #chatBox class="chat-history">
            @for (msg of chatHistory; track msg) {
              <div [style.text-align]="msg.role === 'user' ? 'right' : 'left'" class="msg-row">
                <div class="msg-bubble" [class.user-msg]="msg.role === 'user'" [class.assistant-msg]="msg.role === 'assistant'">
                  <div [innerHTML]="renderMarkdown(msg.content)"></div>
                  @if (msg.cliBackendUsed) {
                    <div class="muted msg-meta">CLI backend: {{ msg.cliBackendUsed }}</div>
                  }
                  @if (msg.routing) {
                    <div class="muted msg-meta">Routing: requested={{ msg.routing.requestedBackend || 'n/a' }}, effective={{ msg.routing.effectiveBackend || msg.cliBackendUsed || 'n/a' }}, reason={{ msg.routing.reason || 'n/a' }}</div>
                  }
                  @if (msg.contextMeta) {
                    <div class="context-panel">
                      <div class="context-title">Context Debug</div>
                      <div class="context-meta">
                        policy={{ msg.contextMeta.policy_version || 'v1' }} |
                        chunks={{ msg.contextMeta.chunk_count || 0 }} |
                        tokens={{ msg.contextMeta.token_estimate || 0 }}
                      </div>
                      <div class="context-meta">strategy={{ msg.contextMeta.strategy | json }}</div>
                      @if (msg.contextSources?.length) {
                        <div class="context-sources">
                          @for (c of msg.contextSources; track c) {
                            <div class="context-source-row">
                              <div>[{{ c.engine }}] {{ c.source }}</div>
                              <div class="context-actions">
                                <button class="mini-btn" (click)="previewSource(c)">Preview</button>
                                <button class="mini-btn" (click)="copySourcePath(c.source)">Copy Path</button>
                              </div>
                              @if (c.previewLoading) {
                                <pre class="source-preview">Loading...</pre>
                              }
                              @if (c.previewError) {
                                <pre class="source-preview">{{ c.previewError }}</pre>
                              }
                              @if (c.preview) {
                                <pre class="source-preview">{{ c.preview }}</pre>
                              }
                            </div>
                          }
                        </div>
                      }
                    </div>
                  }
                  @if (msg.sgptCommand) {
                    <div class="sgpt-section">
                      <div class="sgpt-label">
                        <strong>Shell command:</strong>
                        <pre class="sgpt-code">{{msg.sgptCommand}}</pre>
                      </div>
                      <div class="sgpt-actions">
                        <button (click)="executeSgpt(msg)" class="confirm-btn">Run</button>
                        <button (click)="msg.sgptCommand = undefined" class="cancel-btn">Ignore</button>
                      </div>
                    </div>
                  }
                  @if (msg.requiresConfirmation) {
                    <div class="sgpt-section">
                      <div class="plan-header">Planned actions</div>
                      @if (msg.planRisk) {
                        <div class="muted plan-risk">Risk: <strong>{{ msg.planRisk.level }}</strong> - {{ msg.planRisk.reason }}</div>
                      }
                      @for (tc of msg.toolCalls; track tc) {
                        <div class="tool-card">
                          <div><strong>{{ formatToolName(tc?.name) }}</strong></div>
                          <div class="muted tool-meta">Scope: {{ summarizeToolScope(tc) }}</div>
                          <div class="muted tool-meta">Expected: {{ summarizeToolImpact(tc) }}</div>
                          <div class="muted tool-meta">Changes: {{ summarizeToolChanges(tc) }}</div>
                          <details class="raw-args">
                            <summary style="cursor: pointer;">Raw args</summary>
                            <pre class="raw-args-code">{{ tc?.args | json }}</pre>
                          </details>
                        </div>
                      }
                      <div class="muted confirm-hint">Type <strong>RUN</strong> to confirm execution.</div>
                      <input [(ngModel)]="msg.confirmationText" placeholder="Type RUN" class="confirm-input" />
                      <div class="sgpt-actions">
                        <button (click)="confirmAction(msg)" class="confirm-btn" [disabled]="(msg.confirmationText || '').trim().toUpperCase() !== 'RUN'">Run Plan</button>
                        <button (click)="cancelAction(msg)" class="cancel-btn">Cancel Plan</button>
                      </div>
                    </div>
                  }
                </div>
              </div>
            }
            @if (busy) {
              <div class="muted working-text">Working...</div>
            }
          </div>
          <div class="input-area">
            <input data-testid="assistant-dock-input" [(ngModel)]="chatInput" (keyup.enter)="sendChat()" placeholder="Ask me anything..." [disabled]="busy">
            <button (click)="sendChat()" [disabled]="busy || !chatInput.trim()">Send</button>
            @if (lastFailedRequest && !busy) {
              <button class="cancel-btn" (click)="retryLastFailed()">Retry last</button>
            }
          </div>
          <label class="hybrid-toggle">
            <input type="checkbox" [(ngModel)]="useHybridContext" [disabled]="busy">
            Hybrid Context (Aider + Vibe + LlamaIndex)
          </label>
          <div class="muted context-info">Route: {{ runtimeContext.route }} | User: {{ runtimeContext.userName || 'n/a' }} ({{ runtimeContext.userRole || 'n/a' }}) | Agents: {{ runtimeContext.agents.length }} | Teams: {{ runtimeContext.teamsCount }} | Templates: {{ runtimeContext.templatesCount }}</div>
          <div class="row quick-actions-row">
            <button class="mini-btn" (click)="refreshRuntimeContext()">Refresh Context</button>
            @for (qa of quickActions(); track qa.label) {
              <button class="mini-btn" (click)="runQuickAction(qa.prompt)" [disabled]="busy">{{ qa.label }}</button>
            }
          </div>
          <label class="hybrid-toggle">
            CLI Backend:
            <select [(ngModel)]="cliBackend" [disabled]="busy" (ngModelChange)="onCliBackendChange()">
              @for (backend of availableCliBackends; track backend) {
                <option [value]="backend">{{ backendLabel(backend) }}</option>
              }
            </select>
          </label>
          <div class="muted context-info">Actions require admin rights and confirmation.</div>
        </div>
      }
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
    @media (max-width: 900px) {
      .ai-assistant-container {
        right: 0;
        left: 0;
        bottom: 0;
        width: auto;
      }
      .ai-assistant-container:not(.minimized) {
        height: 100dvh;
        border-radius: 0;
      }
      .ai-assistant-container:not(.minimized) .header {
        border-radius: 0;
      }
      .content {
        height: calc(100dvh - 56px);
      }
      .input-area {
        flex-wrap: wrap;
      }
      .input-area input {
        width: 100%;
      }
    }
    </style>
    `
})
export class AiAssistantComponent implements OnInit, AfterViewChecked {
  private dir = inject(AgentDirectoryService);
  private agentApi = inject(AgentApiService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);
  private auth = inject(UserAuthService);
  private domain = inject(AiAssistantDomainService);
  private router = inject(Router);
  private zone = inject(NgZone);
  private cdr = inject(ChangeDetectorRef);

  @ViewChild('chatBox') private chatBox?: ElementRef;

  minimized = true;
  busy = false;
  chatInput = '';
  useHybridContext = false;
  cliBackend: CliBackend = 'auto';
  availableCliBackends: CliBackend[] = ['auto', 'sgpt', 'opencode', 'aider', 'mistral_code'];
  chatHistory: ChatMessage[] = [];
  lastFailedRequest?: { mode: 'hybrid' | 'chat'; prompt: string };
  private readonly pendingPlanStorageKey = 'ananta.ai-assistant.pending-plan';
  private readonly historyStorageKey = 'ananta.ai-assistant.history.v1';
  runtimeContext: AssistantRuntimeContext = {
    route: '/',
    agents: [],
    teamsCount: 0,
    templatesCount: 0,
    hasConfig: false,
  };

  get hub() {
    return this.dir.list().find(a => a.role === 'hub') || this.dir.list()[0];
  }

  ngOnInit() {
    this.restoreChatHistory();
    if (!this.chatHistory.length) {
      this.chatHistory.push({ role: 'assistant', content: 'Hello. I am your AI assistant.' });
    }
    this.loadCliBackend();
    this.restorePendingPlan();
    this.refreshRuntimeContext();
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe(() => this.refreshRuntimeContext());
  }

  ngAfterViewChecked() {
    this.scrollToBottom();
  }

  toggleMinimize() {
    this.minimized = !this.minimized;
  }

  refreshRuntimeContext() {
    const hub = this.hub;
    const decodedUser: any = this.auth.decodeTokenPayload(this.auth.token);
    const route = this.router.url || '/';
    const agents = this.dir.list().map(a => ({ name: a.name, role: a.role, url: a.url }));
    const selectedAgentName = route.startsWith('/panel/') ? decodeURIComponent(route.split('/panel/')[1]?.split('?')[0] || '') : undefined;

    const baseCtx: AssistantRuntimeContext = {
      route,
      selectedAgentName,
      userRole: decodedUser?.role,
      userName: decodedUser?.sub,
      agents,
      teamsCount: 0,
      templatesCount: 0,
      hasConfig: false,
    };

    if (!hub) {
      this.runtimeContext = baseCtx;
      return;
    }

    this.hubApi.getAssistantReadModel(hub.url).subscribe({
      next: (res) => {
        const teams = Array.isArray(res?.teams?.items) ? res.teams.items : [];
        const templates = Array.isArray(res?.templates?.items) ? res.templates.items : [];
        const effectiveAgents = Array.isArray(res?.agents?.items)
          ? res.agents.items.map((a: any) => ({ name: String(a?.name || ''), role: a?.role, url: String(a?.url || '') })).filter((a: any) => a.name && a.url)
          : (Array.isArray(res?.agents) ? res.agents : []);
        const mappedAgents = Array.isArray(effectiveAgents) && effectiveAgents.length ? effectiveAgents : agents;
        this.runtimeContext = {
          ...baseCtx,
          agents: mappedAgents,
          teamsCount: teams.length,
          templatesCount: templates.length,
          hasConfig: !!res?.config?.effective,
          configSnapshot: this.toCompactConfigSnapshot(res?.config?.effective || {}),
        };
        this.cdr.detectChanges();
      },
      error: () => {
        forkJoin({
          config: this.agentApi.getConfig(hub.url),
          teams: this.hubApi.listTeams(hub.url),
          templates: this.hubApi.listTemplates(hub.url),
          agents: this.hubApi.listAgents(hub.url),
        }).subscribe({
          next: (legacyRes) => {
            const teams = Array.isArray(legacyRes.teams) ? legacyRes.teams : [];
            const templates = Array.isArray(legacyRes.templates) ? legacyRes.templates : [];
            const effectiveAgents = Array.isArray(legacyRes.agents)
              ? legacyRes.agents.map((a: any) => ({ name: String(a?.name || ''), role: a?.role, url: String(a?.url || '') })).filter((a: any) => a.name && a.url)
              : agents;
            this.runtimeContext = {
              ...baseCtx,
              agents: effectiveAgents,
              teamsCount: teams.length,
              templatesCount: templates.length,
              hasConfig: !!legacyRes.config,
              configSnapshot: this.toCompactConfigSnapshot(legacyRes.config),
            };
            this.cdr.detectChanges();
          },
          error: () => {
            this.runtimeContext = baseCtx;
            this.cdr.detectChanges();
          }
        });
      }
    });
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
    const context = this.buildAssistantRequestContext();

    this.chatHistory.push({ role: 'user', content: userMsg });
    this.persistChatHistory();
    this.chatInput = '';
    this.busy = true;
    const assistantMsg: ChatMessage = { role: 'assistant', content: '' };
    this.chatHistory.push(assistantMsg);

    if (this.useHybridContext) {
      const hybridPrompt = `Project context:\n${JSON.stringify(context, null, 2)}\n\nUser request:\n${userMsg}`;
      this.agentApi.sgptExecute(hub.url, hybridPrompt, [], undefined, true, this.cliBackend).subscribe({
        next: r => {
          this.zone.run(() => {
            const output = typeof r?.output === 'string' ? r.output : '';
            assistantMsg.content = output && output.trim() ? output : 'Empty SGPT response';
            if (typeof r?.backend === 'string' && r.backend) {
              assistantMsg.cliBackendUsed = r.backend;
            }
            if (r?.routing && typeof r.routing === 'object') {
              assistantMsg.routing = {
                requestedBackend: r.routing.requested_backend,
                effectiveBackend: r.routing.effective_backend,
                reason: r.routing.reason,
                policyVersion: r.routing.policy_version,
              };
            }
            if (r?.context) {
              assistantMsg.contextMeta = r.context;
            }
            this.lastFailedRequest = undefined;
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
            this.persistChatHistory();
            this.cdr.detectChanges();
          });
        },
        error: (e) => {
          this.zone.run(() => {
            this.ns.error('Hybrid SGPT failed');
            assistantMsg.content = 'Error: ' + (e?.error?.message || e?.message || 'Hybrid SGPT failed');
            assistantMsg.recoverableError = true;
            this.lastFailedRequest = { mode: 'hybrid', prompt: userMsg };
            this.busy = false;
            this.persistChatHistory();
            this.cdr.detectChanges();
          });
        },
        complete: () => { this.zone.run(() => { this.busy = false; this.cdr.detectChanges(); }); }
      });
      return;
    }

    this.agentApi.llmGenerate(hub.url, userMsg, null, undefined, { history, context }).subscribe({
      next: r => {
        this.zone.run(() => {
          const responseText = typeof r?.response === 'string' ? r.response : '';
          if (r?.requires_confirmation && Array.isArray(r.tool_calls)) {
            assistantMsg.content = responseText && responseText.trim() ? responseText : 'Pending actions require confirmation.';
            assistantMsg.requiresConfirmation = true;
            assistantMsg.toolCalls = r.tool_calls;
            assistantMsg.pendingPrompt = userMsg;
            assistantMsg.planRisk = this.assessPlanRisk(r.tool_calls);
            this.storePendingPlan(assistantMsg);
          } else if (!responseText || !responseText.trim()) {
            this.ns.error('Empty LLM response');
            assistantMsg.content = '';
          } else {
            assistantMsg.content = responseText;
            this.checkForSgptCommand(assistantMsg);
            this.lastFailedRequest = undefined;
          }
          this.persistChatHistory();
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
          assistantMsg.recoverableError = true;
          this.lastFailedRequest = { mode: 'chat', prompt: userMsg };
          this.busy = false;
          this.persistChatHistory();
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
    this.clearPendingPlan();

    this.agentApi.llmGenerate(hub.url, prompt, null, undefined, {
      history,
      context: this.buildAssistantRequestContext(),
      tool_calls: toolCalls,
      confirm_tool_calls: true
    }).subscribe({
      next: r => {
        const summary = toolCalls.map(tc => `- ${this.formatToolName(tc?.name)}: ${this.summarizeToolChanges(tc)}`).join('\n');
        const msgText = `${r.response || 'Actions completed.'}\n\nApplied changes:\n${summary}`;
        this.chatHistory.push({ role: 'assistant', content: msgText });
        this.persistChatHistory();
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
    this.clearPendingPlan();
    this.chatHistory.push({ role: 'assistant', content: 'Pending actions cancelled.' });
    this.persistChatHistory();
  }

  retryLastFailed() {
    if (!this.lastFailedRequest || this.busy) return;
    this.chatInput = this.lastFailedRequest.prompt;
    this.sendChat();
  }

  formatToolName(name?: string): string {
    return this.domain.formatToolName(name);
  }

  summarizeToolScope(tc: any): string {
    return this.domain.summarizeToolScope(tc);
  }

  summarizeToolImpact(tc: any): string {
    return this.domain.summarizeToolImpact(tc);
  }

  summarizeToolChanges(tc: any): string {
    return this.domain.summarizeToolChanges(tc);
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
        this.persistChatHistory();
      },
      error: (err) => {
        this.ns.error('Execution failed');
        this.chatHistory.push({ role: 'assistant', content: 'Error: ' + (err.error?.error || err.message) });
        this.persistChatHistory();
        this.busy = false;
      },
      complete: () => { this.busy = false; }
    });
  }

  private checkForSgptCommand(msg: ChatMessage) {
    msg.sgptCommand = this.domain.extractSgptCommand(msg.content);
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

  private assessPlanRisk(toolCalls: any[]): { level: 'low' | 'medium' | 'high'; reason: string } {
    return this.domain.assessPlanRisk(toolCalls);
  }

  private storePendingPlan(msg: ChatMessage) {
    if (!msg.pendingPrompt || !Array.isArray(msg.toolCalls) || !msg.toolCalls.length) return;
    try {
      localStorage.setItem(
        this.pendingPlanStorageKey,
        JSON.stringify({
          pendingPrompt: msg.pendingPrompt,
          toolCalls: msg.toolCalls,
          createdAt: Date.now(),
        })
      );
    } catch {}
  }

  private restorePendingPlan() {
    try {
      const raw = localStorage.getItem(this.pendingPlanStorageKey);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!parsed?.pendingPrompt || !Array.isArray(parsed?.toolCalls) || !parsed.toolCalls.length) return;
      this.chatHistory.push({
        role: 'assistant',
        content: 'Restored pending action plan from last session.',
        requiresConfirmation: true,
        pendingPrompt: String(parsed.pendingPrompt),
        toolCalls: parsed.toolCalls,
        planRisk: this.assessPlanRisk(parsed.toolCalls),
      });
      this.persistChatHistory();
    } catch {}
  }

  private clearPendingPlan() {
    try {
      localStorage.removeItem(this.pendingPlanStorageKey);
    } catch {}
  }

  private buildAssistantRequestContext() {
    return {
      route: this.runtimeContext.route,
      selected_agent: this.runtimeContext.selectedAgentName || null,
      user: {
        name: this.runtimeContext.userName || null,
        role: this.runtimeContext.userRole || null,
      },
      agents: this.runtimeContext.agents,
      teams_count: this.runtimeContext.teamsCount,
      templates_count: this.runtimeContext.templatesCount,
      has_config: this.runtimeContext.hasConfig,
      config_snapshot: this.runtimeContext.configSnapshot || null,
    };
  }

  quickActions(): Array<{ label: string; prompt: string }> {
    return this.domain.quickActions(this.runtimeContext.route || '/');
  }

  runQuickAction(prompt: string) {
    if (this.busy) return;
    this.chatInput = prompt;
    this.sendChat();
  }

  private toCompactConfigSnapshot(cfg: any) {
    if (!cfg || typeof cfg !== 'object') return null;
    return {
      default_provider: cfg.default_provider || null,
      default_model: cfg.default_model || null,
      template_agent_name: cfg.template_agent_name || null,
      team_agent_name: cfg.team_agent_name || null,
      sgpt_execution_backend: cfg.sgpt_execution_backend || null,
      llm_config: cfg.llm_config ? {
        provider: cfg.llm_config.provider || null,
        model: cfg.llm_config.model || null,
        lmstudio_api_mode: cfg.llm_config.lmstudio_api_mode || null,
      } : null,
    };
  }

  private persistChatHistory() {
    this.domain.persistHistory(this.historyStorageKey, this.chatHistory);
  }

  private restoreChatHistory() {
    this.chatHistory = this.domain.restoreHistory(this.historyStorageKey);
  }
}
