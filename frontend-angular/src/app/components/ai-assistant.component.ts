import { ChangeDetectorRef, Component, ElementRef, NgZone, OnDestroy, OnInit, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NavigationEnd, Router } from '@angular/router';
import { filter, forkJoin } from 'rxjs';

import { WindowBridgeService } from '../services/window-bridge.service';
import { AiSnakeConfigPanelComponent } from './ai-snake-config-panel.component';
import { AiSnakeSharePanelComponent } from './ai-snake-share-panel.component';
import { AiSnakeChatPanelComponent } from './ai-snake-chat-panel.component';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { AiAssistantControlsComponent } from './ai-assistant-controls.component';
import { AiAssistantDomainService } from './ai-assistant-domain.service';
import { AiAssistantMessageListComponent } from './ai-assistant-message-list.component';
import { AiAssistantStorageService } from './ai-assistant-storage.service';
import { AssistantRuntimeContext, ChatMessage, ChatThread, CliBackend, ContextSource } from './ai-assistant.types';

@Component({
  standalone: true,
  selector: 'app-ai-assistant',
  imports: [CommonModule, AiAssistantMessageListComponent, AiAssistantControlsComponent, AiSnakeConfigPanelComponent, AiSnakeSharePanelComponent, AiSnakeChatPanelComponent],
  templateUrl: './ai-assistant.component.html'
})
export class AiAssistantComponent implements OnInit, OnDestroy {
  private dir = inject(AgentDirectoryService);
  private agentApi = inject(AgentApiService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);
  private auth = inject(UserAuthService);
  private domain = inject(AiAssistantDomainService);
  private storage = inject(AiAssistantStorageService);
  private router = inject(Router);
  private zone = inject(NgZone);
  private cdr = inject(ChangeDetectorRef);
  readonly bridge = inject(WindowBridgeService);

  @ViewChild('snakeCanvas') private snakeCanvasRef?: ElementRef<HTMLCanvasElement>;
  snakeVisible = false;
  configPanelOpen = false;
  sharePanelOpen = false;
  snakeChatPanelOpen = false;
  snakeChatPanelTab: 'chat' | 'login' | 'pair' | 'mode' | 'settings' | 'deprecated' = 'chat';
  private snakeDrawHandle: number | null = null;

  minimized = true;
  busy = false;
  chatInput = '';
  useHybridContext = false;
  cliBackend: CliBackend = 'auto';
  availableCliBackends: CliBackend[] = ['auto', 'sgpt', 'codex', 'opencode', 'aider', 'mistral_code'];
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
    this.loadCliBackend();
    this.restorePendingPlan();
    this.refreshRuntimeContext();
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe(() => this.refreshRuntimeContext());
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
    const thread: ChatThread = {
      id: `thread-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      title: `Chat ${index}`,
      history: [{ role: 'assistant', content: 'Hallo. Ich bin AI Snake.' }],
      updatedAt: Date.now(),
    };
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
          templatesSummary: this.toTemplateSummary(templates),
          settingsSummary: res?.settings?.summary || null,
          editableSettings: this.toEditableSettingsSummary(res?.settings?.editable_inventory),
          automationSummary: res?.automation || null,
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
              templatesSummary: this.toTemplateSummary(templates),
              settingsSummary: this.toLegacySettingsSummary(legacyRes.config),
              editableSettings: [],
              automationSummary: null,
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
                  assistantMsg.contextMeta = {
                    ...(assistantMsg.contextMeta || {}),
                    policy_version: ctx?.policy_version || assistantMsg.contextMeta?.policy_version,
                    chunk_count: typeof ctx?.chunk_count === 'number' ? ctx.chunk_count : chunks.length,
                    token_estimate: typeof ctx?.token_estimate === 'number' ? ctx.token_estimate : assistantMsg.contextMeta?.token_estimate,
                    strategy: ctx?.strategy || assistantMsg.contextMeta?.strategy,
                    explainability: ctx?.explainability || assistantMsg.contextMeta?.explainability,
                  };
                  assistantMsg.contextSources = chunks.map((c: any) => ({
                    engine: c.engine,
                    source: c.source,
                    score: c.score,
                    recordKind: c?.metadata?.record_kind,
                    artifactId: c?.metadata?.artifact_id,
                    knowledgeIndexId: c?.metadata?.knowledge_index_id,
                    collectionNames: Array.isArray(c?.metadata?.collection_names) ? c.metadata.collection_names : [],
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
        const toolResults = Array.isArray((r as any)?.tool_results) ? (r as any).tool_results : [];
        const resultsText = toolResults.length
          ? `\n\nTool results:\n${toolResults.map((tr: any) => `- ${tr?.tool || 'tool'}: ${tr?.success ? 'ok' : 'failed'}${tr?.error ? ` (${tr.error})` : ''}`).join('\n')}`
          : '';
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
        if (supported.includes('codex')) dynamic.push('codex');
        if (supported.includes('opencode')) dynamic.push('opencode');
        if (supported.includes('aider')) dynamic.push('aider');
        if (supported.includes('mistral_code')) dynamic.push('mistral_code');
        this.availableCliBackends = dynamic;
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
          (value === 'auto' || value === 'sgpt' || value === 'codex' || value === 'opencode' || value === 'aider' || value === 'mistral_code') &&
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
  }

  private persistDockVisibility() {
    this.storage.persistBoolean(this.dockHiddenStorageKey, this.hidden);
  }

  private clearPendingPlan() {
    this.storage.clear(this.pendingPlanStorageKey);
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
      templates_summary: this.runtimeContext.templatesSummary,
      settings_summary: this.runtimeContext.settingsSummary || null,
      editable_settings: this.runtimeContext.editableSettings,
      automation_summary: this.runtimeContext.automationSummary || null,
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
      codex_cli: cfg.codex_cli ? {
        base_url: cfg.codex_cli.base_url || null,
        api_key_profile: cfg.codex_cli.api_key_profile || null,
        prefer_lmstudio: cfg.codex_cli.prefer_lmstudio ?? null,
      } : null,
    };
  }

  private toTemplateSummary(templates: any[]): Array<{ name: string; description?: string }> {
    if (!Array.isArray(templates)) return [];
    const maxTemplates = 25;
    const maxDescriptionChars = 180;

    return templates
      .flatMap((tpl: any) => {
        const name = String(tpl?.name || '').trim();
        if (!name) return [];
        const rawDescription = String(tpl?.description || '').replace(/\s+/g, ' ').trim();
        const description = rawDescription ? rawDescription.slice(0, maxDescriptionChars) : undefined;
        return [description ? { name, description } : { name }];
      })
      .slice(0, maxTemplates);
  }

  private toEditableSettingsSummary(items: any[]): Array<{ key: string; path?: string; type?: string; endpoint?: string }> {
    if (!Array.isArray(items)) return [];
    return items
      .map((item: any) => ({
        key: String(item?.key || '').trim(),
        path: item?.path ? String(item.path) : undefined,
        type: item?.type ? String(item.type) : undefined,
        endpoint: item?.endpoint ? String(item.endpoint) : undefined,
      }))
      .filter((item) => !!item.key)
      .slice(0, 60);
  }

  private toLegacySettingsSummary(cfg: any) {
    if (!cfg || typeof cfg !== 'object') return null;
    return {
      llm: {
        default_provider: cfg.default_provider || null,
        default_model: cfg.default_model || null,
      },
      system: {
        log_level: cfg.log_level || null,
        http_timeout: cfg.http_timeout ?? null,
        command_timeout: cfg.command_timeout ?? null,
      },
    };
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
    const compactThreads = (Array.isArray(this.chatThreads) ? this.chatThreads : []).map((thread) => ({
      id: thread.id,
      title: thread.title,
      updatedAt: thread.updatedAt,
      history: thread.history.slice(-40).map((message) => ({ role: message.role, content: message.content })),
    }));
    this.storage.persistJson(this.threadStorageKey, compactThreads);
    this.storage.persistJson(this.activeThreadStorageKey, this.activeThreadId);
  }

  private restoreThreads() {
    const stored = this.storage.restoreJson<any[]>(this.threadStorageKey, []);
    const threads = Array.isArray(stored)
      ? stored
          .map((thread: any) => {
            const rawHistory = Array.isArray(thread?.history) ? thread.history : [];
            const history = rawHistory
              .filter((message: any) => (message?.role === 'user' || message?.role === 'assistant') && typeof message?.content === 'string')
              .map((message: any) => ({ role: message.role, content: message.content } as ChatMessage))
              .slice(-40);
            const id = String(thread?.id || '').trim();
            if (!id) return null;
            return {
              id,
              title: String(thread?.title || '').trim() || 'Chat',
              history,
              updatedAt: Number(thread?.updatedAt) || Date.now(),
            } as ChatThread;
          })
          .filter((thread): thread is ChatThread => !!thread)
      : [];

    if (threads.length) {
      this.chatThreads = threads;
      const active = this.storage.restoreJson<string>(this.activeThreadStorageKey, '');
      this.activeThreadId = typeof active === 'string' ? active : '';
      return;
    }

    this.restoreChatHistory();
    this.chatThreads = [
      {
        id: 'thread-default',
        title: 'Chat 1',
        history: this.chatHistory.length ? this.chatHistory : [{ role: 'assistant', content: 'Hallo. Ich bin AI Snake.' }],
        updatedAt: Date.now(),
      },
    ];
    this.activeThreadId = 'thread-default';
  }

  private ensureThreadSelection() {
    if (!this.chatThreads.length) {
      this.chatThreads = [
        {
          id: 'thread-default',
          title: 'Chat 1',
          history: [{ role: 'assistant', content: 'Hallo. Ich bin AI Snake.' }],
          updatedAt: Date.now(),
        },
      ];
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
    const normalized = prompt.replace(/\s+/g, ' ').trim();
    if (!normalized) return;
    const isDefaultTitle = /^Chat \d+$/.test(active.title) || active.title === 'Chat';
    if (!isDefaultTitle) return;
    active.title = normalized.length > 30 ? `${normalized.slice(0, 30)}...` : normalized;
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
    this.sharePanelOpen = !this.sharePanelOpen;
    if (this.sharePanelOpen) {
      this.configPanelOpen = false;
      this.snakeChatPanelOpen = false;
    }
  }

  toggleSnakeChatPanel(): void {
    this.snakeChatPanelOpen = !this.snakeChatPanelOpen;
    if (this.snakeChatPanelOpen) {
      this.configPanelOpen = false;
      this.sharePanelOpen = false;
    }
  }

  openSnakeChatPanelTab(tab: 'chat' | 'login' | 'pair' | 'mode' | 'settings' | 'deprecated'): void {
    this.snakeChatPanelTab = tab;
    this.snakeChatPanelOpen = true;
    this.configPanelOpen = false;
    this.sharePanelOpen = false;
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
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const p = (this.bridge.state$.value?.payload || {}) as Record<string, unknown>;
    const bw = Math.max(1, Number(p['board_w']) || 24);
    const bh = Math.max(1, Number(p['board_h']) || 8);
    const W = canvas.width;
    const H = canvas.height;
    const cw = W / bw;
    const ch = H / bh;

    const COLORS: Record<string, string> = {
      mint: '#7fffd4', cyan: '#22d3ee', violet: '#a78bfa', amber: '#fbbf24', rose: '#fb7185',
    };
    const col = COLORS[String(p['snake_color'] || 'mint')] ?? '#7fffd4';

    ctx.fillStyle = '#0b1220';
    ctx.fillRect(0, 0, W, H);

    ctx.strokeStyle = '#131e36';
    ctx.lineWidth = 0.5;
    for (let x = 0; x <= bw; x++) {
      ctx.beginPath(); ctx.moveTo(x * cw, 0); ctx.lineTo(x * cw, H); ctx.stroke();
    }
    for (let y = 0; y <= bh; y++) {
      ctx.beginPath(); ctx.moveTo(0, y * ch); ctx.lineTo(W, y * ch); ctx.stroke();
    }

    const trail = Array.isArray(p['trail_path']) ? (p['trail_path'] as number[][]) : [];
    ctx.fillStyle = col + '22';
    for (const [x, y] of trail) {
      ctx.fillRect(x * cw + 1, y * ch + 1, cw - 2, ch - 2);
    }

    const snake = Array.isArray(p['snake']) ? (p['snake'] as number[][]) : [];
    ctx.fillStyle = col + 'aa';
    for (let i = 1; i < snake.length; i++) {
      const [x, y] = snake[i];
      ctx.fillRect(x * cw + 1, y * ch + 1, cw - 2, ch - 2);
    }
    if (snake.length > 0) {
      ctx.fillStyle = col;
      const [hx, hy] = snake[0];
      ctx.fillRect(hx * cw, hy * ch, cw, ch);
    }

    if (p['paused']) {
      ctx.fillStyle = 'rgba(11,18,32,0.65)';
      ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = col;
      ctx.font = `bold ${Math.round(ch * 0.75)}px ui-monospace,monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('PAUSED', W / 2, H / 2);
    }

    this.snakeDrawHandle = requestAnimationFrame(() => this.drawSnakeFrame());
  }
}
