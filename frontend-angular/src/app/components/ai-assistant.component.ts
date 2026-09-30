import {
  ChangeDetectorRef,
  Component,
  DestroyRef,
  ElementRef,
  Input,
  NgZone,
  OnDestroy,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import { NavigationEnd, Router } from '@angular/router';
import { filter, forkJoin } from 'rxjs';

import { WindowBridgeService } from '../services/window-bridge.service';
import { SnakeOverlayService } from '../services/snake-overlay.service';
import { renderSnakeCanvas } from './snake-canvas-renderer';
import { AiSnakeConfigPanelComponent } from './ai-snake-config-panel.component';
import { AiSnakeSharePanelComponent } from './ai-snake-share-panel.component';
import { AiSnakeChatPanelComponent } from './ai-snake-chat-panel.component';
import { AiSnakePairDevPanelComponent } from './ai-snake-pair-dev-panel.component';
import { AiSnakePanelTab, isAiSnakePanelTab } from './ai-snake-panel-tab';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { ShareSessionService } from '../services/share-session.service';
import { AiAssistantControlsComponent } from './ai-assistant-controls.component';
import { AiAssistantDomainService } from './ai-assistant-domain.service';
import { AiAssistantMessageListComponent } from './ai-assistant-message-list.component';
import { AiAssistantStorageService } from './ai-assistant-storage.service';
import { AssistantRuntimeContext, ChatMessage, ChatThread, CliBackend, ContextSource } from './ai-assistant.types';
import {
  contextFromLegacyResponses,
  contextFromReadModel,
  toAssistantRequestContext,
} from './ai-assistant-runtime-context.mappers';
import {
  formatExecutionOutput,
  formatToolResults,
  isCliBackend,
  mergeContextMeta,
  toAvailableCliBackends,
  toContextSources,
  toRoutingMeta,
} from './ai-assistant-response.mappers';
import {
  createDefaultThread,
  createNumberedThread,
  deriveThreadTitle,
  parseStoredThreads,
  toStoredThreads,
} from './ai-assistant-thread.mappers';

@Component({
  standalone: true,
  selector: 'app-ai-assistant',
  imports: [
    AiAssistantMessageListComponent,
    AiAssistantControlsComponent,
    AiSnakeConfigPanelComponent,
    AiSnakeSharePanelComponent,
    AiSnakeChatPanelComponent,
    AiSnakePairDevPanelComponent,
  ],
  templateUrl: './ai-assistant.component.html'
})
export class AiAssistantComponent implements OnInit, OnDestroy {
  private dir = inject(AgentDirectoryService);
  private agentApi = inject(AgentApiService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);
  private auth = inject(UserAuthService);
  private shares = inject(ShareSessionService);
  private domain = inject(AiAssistantDomainService);
  private storage = inject(AiAssistantStorageService);
  private router = inject(Router);
  private zone = inject(NgZone);
  private cdr = inject(ChangeDetectorRef);
  private destroyRef = inject(DestroyRef);
  readonly bridge = inject(WindowBridgeService);
  readonly snakeOverlay = inject(SnakeOverlayService);

  private pairOnlyValue = false;
  private initialized = false;
  private hubRuntimeInitialized = false;
  private pairRouteActive = false;

  @Input()
  set pairOnly(value: boolean) {
    this.pairOnlyValue = value;
    if (value) {
      this.configPanelOpen = false;
      this.sharePanelOpen = false;
      if (this.pairDevMounted
        && this.snakeChatPanelTab !== 'pair'
        && this.snakeChatPanelTab !== 'media') {
        this.snakeChatPanelTab = 'pair';
        this.snakeChatPanelOpen = true;
      }
    }
    if (this.initialized && !value) this.initializeHubRuntime();
  }

  get pairOnly(): boolean {
    return this.pairOnlyValue;
  }

  get snakeChatPanelVisible(): boolean {
    return !this.pairOnly
      && this.snakeChatPanelOpen
      && this.snakeChatPanelTab !== 'pair'
      && this.snakeChatPanelTab !== 'media';
  }

  /**
   * The local LLM thread picker belongs to the unobscured assistant surface.
   * Overlay workspaces keep their own chat/session navigation and must not
   * expose controls that only mutate a hidden conversation underneath them.
   */
  get generalAssistantSurfaceVisible(): boolean {
    return !this.pairOnly
      && !this.configPanelOpen
      && !this.sharePanelOpen
      && !this.snakeChatPanelOpen;
  }

  @ViewChild('snakeCanvas') private snakeCanvasRef?: ElementRef<HTMLCanvasElement>;
  snakeVisible = false;
  configPanelOpen = false;
  sharePanelOpen = false;
  snakeChatPanelOpen = false; // initialised in restoreDockState()
  snakeChatPanelTab: AiSnakePanelTab = 'login';
  pairDevMounted = false;
  private snakeDrawHandle: number | null = null;

  minimized = true;
  busy = false;
  chatInput = '';
  useHybridContext = false;
  cliBackend: CliBackend = 'auto';
  availableCliBackends: CliBackend[] = ['auto', 'sgpt', 'codex', 'opencode', 'aider', 'mistral_code'];
  cliBackendMetadata: Record<string, any> = {};
  cliRuntime: Record<string, any> = {};
  chatHistory: ChatMessage[] = [];
  chatThreads: ChatThread[] = [];
  activeThreadId = '';
  threadSwitcherOpen = false;
  lastFailedRequest?: { mode: 'hybrid' | 'chat'; prompt: string };
  private readonly pendingPlanStorageKey = 'ananta.ai-assistant.pending-plan';
  private readonly historyStorageKey = 'ananta.ai-assistant.history.v1';
  private readonly threadStorageKey = 'ananta.ai-assistant.threads.v1';
  private readonly activeThreadStorageKey = 'ananta.ai-assistant.active-thread.v1';
  private readonly dockStateStorageKey = 'ananta.ai-assistant.minimized.v1';
  private readonly dockHiddenStorageKey = 'ananta.ai-assistant.hidden.v1';
  runtimeContext: AssistantRuntimeContext = {
    route: '/',
    agents: [],
    teamsCount: 0,
    templatesCount: 0,
    templatesSummary: [],
    editableSettings: [],
    hasConfig: false,
  };

  get hub() {
    return this.dir.list().find(a => a.role === 'hub') || this.dir.list()[0];
  }

  ngOnInit() {
    this.restoreThreads();
    this.restoreDockState();
    this.ensureThreadSelection();
    this.restorePendingPlan();
    this.initialized = true;
    this.initializeHubRuntime();
    this.handlePairRouteNavigation(this.router.url);
    this.router.events
      .pipe(
        filter((e): e is NavigationEnd => e instanceof NavigationEnd),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(event => {
        if (!this.pairOnly) this.refreshRuntimeContext();
        this.handlePairRouteNavigation(event.urlAfterRedirects);
      });
    this.shares.publicPairRuntimeState$
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.reconcileEmbeddedPairOwner());
  }

  private initializeHubRuntime(): void {
    if (this.pairOnly || this.hubRuntimeInitialized) return;
    this.hubRuntimeInitialized = true;
    this.loadCliBackend();
    this.refreshRuntimeContext();
  }

  toggleMinimize() {
    this.minimized = !this.minimized;
    this.persistDockState();
  }

  hidden = false;

  hideDock() {
    this.hidden = true;
    this.persistDockVisibility();
  }

  showDock() {
    this.hidden = false;
    this.minimized = false;
    this.persistDockVisibility();
    this.persistDockState();
  }

  toggleThreadSwitcher() {
    this.threadSwitcherOpen = !this.threadSwitcherOpen;
  }

  createThread() {
    const index = this.chatThreads.length + 1;
    const thread: ChatThread = createNumberedThread(index);
    this.chatThreads = [...this.chatThreads, thread];
    this.switchThread(thread.id);
    this.threadSwitcherOpen = true;
    this.persistThreads();
  }

  switchThread(threadId: string) {
    const found = this.chatThreads.find((thread) => thread.id === threadId);
    if (!found) return;
    this.activeThreadId = found.id;
    this.chatHistory = found.history;
    this.threadSwitcherOpen = false;
    this.persistThreads();
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
      templatesSummary: [],
      editableSettings: [],
      hasConfig: false,
    };

    if (!hub) {
      this.runtimeContext = baseCtx;
      return;
    }

    this.hubApi.getAssistantReadModel(hub.url).subscribe({
      next: (res) => {
        this.runtimeContext = contextFromReadModel(baseCtx, res, agents);
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
            this.runtimeContext = contextFromLegacyResponses(baseCtx, legacyRes, agents);
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
    this.updateActiveThreadTitle(userMsg);
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
              assistantMsg.routing = toRoutingMeta(r.routing);
            }
            if (r?.context) {
              assistantMsg.contextMeta = r.context;
            }
            this.lastFailedRequest = undefined;
            this.agentApi.sgptContext(hub.url, userMsg, undefined, false).subscribe({
              next: ctx => {
                this.zone.run(() => {
                  const chunks = Array.isArray(ctx?.chunks) ? ctx.chunks : [];
                  assistantMsg.contextMeta = mergeContextMeta(assistantMsg.contextMeta, ctx, chunks);
                  assistantMsg.contextSources = toContextSources(chunks);
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
        const toolResults = Array.isArray((r as any)?.tool_results) ? (r as any).tool_results : [];
        const resultsText = formatToolResults(toolResults);
        const msgText = `${r.response || 'Actions completed.'}\n\nApplied changes:\n${summary}${resultsText}`;
        this.chatHistory.push({ role: 'assistant', content: msgText });
        this.refreshRuntimeContext();
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
        this.chatHistory.push({ role: 'assistant', content: formatExecutionOutput(r) });
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
        this.cliBackendMetadata = data?.supported_backends || {};
        this.availableCliBackends = toAvailableCliBackends(data?.supported_backends);
        this.cliRuntime = (data?.runtime && typeof data.runtime === 'object') ? data.runtime : {};
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
          isCliBackend(value) &&
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

  setCliBackend(backend: CliBackend) {
    this.cliBackend = backend;
    this.onCliBackendChange();
  }

  selectedCliRuntime(): any {
    const effective = this.cliBackend === 'auto' ? this.inferAutoCliBackend() : this.cliBackend;
    return effective ? this.cliRuntime?.[effective] : null;
  }

  private inferAutoCliBackend(): string {
    const snapshot = this.runtimeContext?.configSnapshot || {};
    const configured = String(snapshot?.sgpt_execution_backend || '').trim().toLowerCase();
    if (configured && configured !== 'auto') return configured;
    return 'sgpt';
  }

  private assessPlanRisk(toolCalls: any[]): { level: 'low' | 'medium' | 'high'; reason: string } {
    return this.domain.assessPlanRisk(toolCalls);
  }

  private storePendingPlan(msg: ChatMessage) {
    this.storage.persistPendingPlan(this.pendingPlanStorageKey, msg);
  }

  private restorePendingPlan() {
    const restored = this.storage.restorePendingPlan(this.pendingPlanStorageKey);
    if (!restored) return;
    this.chatHistory.push({
      role: 'assistant',
      content: 'Restored pending action plan from last session.',
      requiresConfirmation: true,
      pendingPrompt: restored.pendingPrompt,
      toolCalls: restored.toolCalls,
      planRisk: this.assessPlanRisk(restored.toolCalls),
    });
    this.persistChatHistory();
  }

  private persistDockState() {
    this.storage.persistBoolean(this.dockStateStorageKey, this.minimized);
  }

  private restoreDockState() {
    this.minimized = this.storage.restoreBoolean(this.dockStateStorageKey, true);
    this.hidden = this.storage.restoreBoolean(this.dockHiddenStorageKey, false);
    // Migrate: old code stored false here; force open on first load after upgrade.
    const storedOpen = localStorage.getItem('ananta.ai-snake.panel-open.v1');
    if (storedOpen === 'false') localStorage.removeItem('ananta.ai-snake.panel-open.v1');
    this.snakeChatPanelOpen = this.storage.restoreBoolean('ananta.ai-snake.panel-open.v1', true);
    const panelTabStorageKey = 'ananta.ai-snake.panel-tab.v1';
    const savedTab = this.storage.restoreJson<string>(panelTabStorageKey, '');
    if (savedTab && !isAiSnakePanelTab(savedTab)) {
      this.snakeChatPanelTab = 'chat';
      this.storage.persistJson(panelTabStorageKey, 'chat');
      return;
    }
    if (isAiSnakePanelTab(savedTab)) {
      if ((savedTab === 'pair' || savedTab === 'media') && !this.isPairDevRoute(this.router.url)) {
        this.snakeChatPanelTab = 'chat';
        this.storage.persistJson(panelTabStorageKey, 'chat');
        return;
      }
      this.snakeChatPanelTab = savedTab;
    }
  }

  private persistDockVisibility() {
    this.storage.persistBoolean(this.dockHiddenStorageKey, this.hidden);
  }

  private clearPendingPlan() {
    this.storage.clear(this.pendingPlanStorageKey);
  }

  private buildAssistantRequestContext() {
    return toAssistantRequestContext(this.runtimeContext);
  }

  quickActions(): Array<{ label: string; prompt: string }> {
    return this.domain.quickActions(this.runtimeContext.route || '/');
  }

  runQuickAction(prompt: string) {
    if (this.busy) return;
    this.chatInput = prompt;
    this.sendChat();
  }

  private persistChatHistory() {
    const threads = Array.isArray(this.chatThreads) ? this.chatThreads : [];
    const active = threads.find((thread) => thread.id === this.activeThreadId);
    if (active) {
      active.history = this.chatHistory.slice(-40).map((msg) => ({ ...msg }));
      active.updatedAt = Date.now();
    }
    this.chatThreads = threads;
    if (threads.length) this.persistThreads();
    this.domain.persistHistory(this.historyStorageKey, this.chatHistory);
  }

  private restoreChatHistory() {
    this.chatHistory = this.domain.restoreHistory(this.historyStorageKey);
  }

  private persistThreads() {
    this.storage.persistJson(this.threadStorageKey, toStoredThreads(this.chatThreads));
    this.storage.persistJson(this.activeThreadStorageKey, this.activeThreadId);
  }

  private restoreThreads() {
    const threads = parseStoredThreads(this.storage.restoreJson<any[]>(this.threadStorageKey, []));

    if (threads.length) {
      this.chatThreads = threads;
      const active = this.storage.restoreJson<string>(this.activeThreadStorageKey, '');
      this.activeThreadId = typeof active === 'string' ? active : '';
      return;
    }

    this.restoreChatHistory();
    this.chatThreads = [createDefaultThread(this.chatHistory.length ? this.chatHistory : [])];
    this.activeThreadId = 'thread-default';
  }

  private ensureThreadSelection() {
    if (!this.chatThreads.length) {
      this.chatThreads = [createDefaultThread()];
      this.activeThreadId = 'thread-default';
    }
    const active = this.chatThreads.find((thread) => thread.id === this.activeThreadId) || this.chatThreads[0];
    this.activeThreadId = active.id;
    this.chatHistory = active.history;
    this.persistThreads();
  }

  private updateActiveThreadTitle(prompt: string) {
    const active = this.chatThreads.find((thread) => thread.id === this.activeThreadId);
    if (!active) return;
    const title = deriveThreadTitle(active.title, prompt);
    if (title !== null) active.title = title;
  }

  ngOnDestroy(): void {
    this.stopSnakeDraw();
  }

  toggleConfigPanel(): void {
    this.configPanelOpen = !this.configPanelOpen;
    if (this.configPanelOpen) {
      this.sharePanelOpen = false;
      this.snakeChatPanelOpen = false;
    }
  }

  toggleSharePanel(): void {
    if (this.pairDevMounted) {
      this.openEmbeddedPairDev();
      return;
    }
    this.sharePanelOpen = !this.sharePanelOpen;
    if (this.sharePanelOpen) {
      this.configPanelOpen = false;
      this.snakeChatPanelOpen = false;
    }
  }

  toggleSnakeChatPanel(): void {
    this.snakeChatPanelOpen = !this.snakeChatPanelOpen;
    this.storage.persistBoolean('ananta.ai-snake.panel-open.v1', this.snakeChatPanelOpen);
    if (this.snakeChatPanelOpen) {
      this.configPanelOpen = false;
      this.sharePanelOpen = false;
    }
  }

  toggleRegionMode(): void { this.snakeOverlay.toggleRegionMode(); }

  onSnakeChatTabChange(tab: AiSnakePanelTab): void {
    if (tab === 'pair' || tab === 'media') {
      this.openPairDev(tab);
      return;
    }
    this.snakeChatPanelTab = tab;
    this.storage.persistJson('ananta.ai-snake.panel-tab.v1', tab);
  }

  openSnakeChatPanelTab(tab: AiSnakePanelTab): void {
    if (tab === 'pair' || tab === 'media') {
      this.openPairDev(tab);
      return;
    }
    this.onSnakeChatTabChange(tab);
    this.snakeChatPanelOpen = true;
    this.storage.persistBoolean('ananta.ai-snake.panel-open.v1', true);
    this.configPanelOpen = false;
    this.sharePanelOpen = false;
  }

  openPairDev(surface: 'pair' | 'media' = 'pair'): void {
    if (this.pairDevMounted) {
      this.openEmbeddedPairDev(surface);
      return;
    }
    if (!this.isPairDevRoute(this.router.url)) {
      void this.router.navigate(['/pair-dev']);
      return;
    }
    this.openEmbeddedPairDev(surface);
  }

  private openEmbeddedPairDev(surface: 'pair' | 'media' = 'pair'): void {
    this.pairDevMounted = true;
    this.hidden = false;
    this.minimized = false;
    this.persistDockVisibility();
    this.persistDockState();
    this.snakeChatPanelTab = surface;
    this.snakeChatPanelOpen = true;
    this.storage.persistBoolean('ananta.ai-snake.panel-open.v1', true);
    this.storage.persistJson('ananta.ai-snake.panel-tab.v1', surface);
    this.configPanelOpen = false;
    this.sharePanelOpen = false;
  }

  private unmountEmbeddedPairDev(): void {
    this.pairDevMounted = false;
    if (this.snakeChatPanelTab !== 'pair' && this.snakeChatPanelTab !== 'media') return;
    this.snakeChatPanelTab = 'chat';
    this.snakeChatPanelOpen = false;
    this.storage.persistBoolean('ananta.ai-snake.panel-open.v1', false);
    this.storage.persistJson('ananta.ai-snake.panel-tab.v1', 'chat');
  }

  private handlePairRouteNavigation(url: string): void {
    const nextPairRouteActive = this.isPairDevRoute(url);
    const enteredPairRoute = nextPairRouteActive && !this.pairRouteActive;
    this.pairRouteActive = nextPairRouteActive;
    if (enteredPairRoute) this.openEmbeddedPairDev();
    this.reconcileEmbeddedPairOwner();
  }

  private reconcileEmbeddedPairOwner(): void {
    if (this.shares.hasPublicPairRuntime) {
      if (!this.pairDevMounted) this.mountEmbeddedPairOwnerInBackground();
      return;
    }
    if (!this.pairRouteActive) this.unmountEmbeddedPairDev();
  }

  private mountEmbeddedPairOwnerInBackground(): void {
    if (this.pairDevMounted) return;
    // Mount only the resource owner. The user's dock visibility, size, active
    // tab and overlays remain untouched until Pair Dev is explicitly opened.
    this.pairDevMounted = true;
  }

  private isPairDevRoute(url: string): boolean {
    return url.split(/[?#]/, 1)[0] === '/pair-dev';
  }

  toggleSnakeCanvas(): void {
    this.snakeVisible = !this.snakeVisible;
    if (this.snakeVisible) {
      setTimeout(() => this.startSnakeDraw(), 60);
    } else {
      this.stopSnakeDraw();
    }
  }

  get snakeBridgeActive(): boolean {
    return this.bridge.isActive;
  }

  get snakeStatusText(): string {
    const p = (this.bridge.state$.value?.payload || {}) as Record<string, unknown>;
    if (!this.bridge.isActive) return 'bridge offline';
    if (!p['active']) return 'snake inaktiv';
    if (p['paused']) return 'pausiert';
    return String(p['ai_snake_runtime_status'] || 'aktiv');
  }

  private startSnakeDraw(): void {
    this.stopSnakeDraw();
    const canvas = this.snakeCanvasRef?.nativeElement;
    if (!canvas) return;
    canvas.width = canvas.offsetWidth || 360;
    canvas.height = 90;
    this.drawSnakeFrame();
  }

  private stopSnakeDraw(): void {
    if (this.snakeDrawHandle !== null) {
      cancelAnimationFrame(this.snakeDrawHandle);
      this.snakeDrawHandle = null;
    }
  }

  private drawSnakeFrame(): void {
    const canvas = this.snakeCanvasRef?.nativeElement;
    if (!canvas || !this.snakeVisible) return;
    renderSnakeCanvas(canvas, (this.bridge.state$.value?.payload || {}) as Record<string, unknown>);
    this.snakeDrawHandle = requestAnimationFrame(() => this.drawSnakeFrame());
  }
}
