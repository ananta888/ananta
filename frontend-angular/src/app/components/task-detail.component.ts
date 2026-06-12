import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { PermissionService } from '../services/permission.service';
import { Subscription, finalize } from 'rxjs';
import { isTaskDone, isTaskInProgress } from '../utils/task-status';
import { TaskStatusDisplayPipe } from '../pipes/task-status-display.pipe';
import { TaskManagementFacade } from '../features/tasks/task-management.facade';
import { TerminalComponent } from './terminal.component';
import { UiSkeletonComponent } from './ui-skeleton.component';
import { decisionExplanation, safetyBoundaryExplanation, userFacingTerm } from '../models/user-facing-language';
import { DecisionExplanationComponent, NextStepAction, NextStepsComponent } from '../shared/ui/display';

@Component({
  standalone: true,
  selector: 'app-task-detail',
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    TaskStatusDisplayPipe,
    TerminalComponent,
    UiSkeletonComponent,
    DecisionExplanationComponent,
    NextStepsComponent,
  ],
  styleUrls: ['./task-detail.component.css'],
  templateUrl: './task-detail.component.html'
})
export class TaskDetailComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private dir = inject(AgentDirectoryService);
  private ns = inject(NotificationService);
  private auth = inject(UserAuthService);
  readonly perm = inject(PermissionService);
  private taskFacade = inject(TaskManagementFacade);

  hub = this.dir.list().find(a => a.role === 'hub');
  task: any;
  subtasks: any[] = [];
  logs: any[] = [];
  allAgents = this.dir.list();
  assignUrl: string | undefined;
  prompt = '';
  proposed = '';
  proposedTouched = false;
  toolCalls: any[] = [];
  comparisons: Record<string, any> | null = null;
  busy = false;
  activeTab = 'details';
  loadingTask = false;
  loadingLogs = false;
  hiddenHints = new Set<string>((localStorage.getItem('ananta.hidden-hints') || '').split(',').filter(Boolean));
  availableProviders: any[] = [];
  instructionProfiles: any[] = [];
  instructionOverlays: any[] = [];
  instructionProfileSelectionId = '';
  instructionOverlaySelectionId = '';
  instructionSessionOverlayId = '';
  instructionSessionId = '';
  instructionSelectionBusy = false;
  instructionCompatibility: any = null;
  isAdmin = false;
  showAdminDrilldown = false;
  taskTerminalMode: 'task' | 'diagnostic' = 'task';
  taskSourcesPayload: any = null;
  taskAnswerVerificationPayload: any = null;
  private routeSub?: Subscription;
  private activeLogTaskId?: string;

  constructor() {
    this.loadProviders();
    this.routeSub = this.route.paramMap.subscribe(() => {
      this.stopStreaming();
      this.proposedTouched = false;
      this.proposed = '';
      this.toolCalls = [];
      this.busy = false; // Sicherheits-Reset bei Task-Wechsel
      this.taskTerminalMode = 'task';
      this.reload();
    });
  }

  ngOnInit() {
    const user = this.auth.decodeTokenPayload(this.auth.token);
    this.isAdmin = user?.role === 'admin';
    if (this.hub?.url) {
      this.taskFacade.connectTaskCollection(this.hub.url);
      this.taskFacade.reloadTaskCollection();
      this.loadInstructionOptions();
    }
  }

  ngOnDestroy() {
    this.stopStreaming();
    this.routeSub?.unsubscribe();
    this.taskFacade.disconnectTaskCollection(this.hub?.url);
  }

  loadProviders() {
    if (!this.hub) return;
    this.taskFacade.listProviderCatalog(this.hub.url).subscribe({
      next: (catalog) => {
        const providers = this.flattenCatalogProviders(catalog);
        if (providers.length) {
          this.availableProviders = providers;
          return;
        }
        this.loadProvidersFallback();
      },
      error: () => this.loadProvidersFallback()
    });
  }

  private flattenCatalogProviders(catalog: any): any[] {
    const blocks = Array.isArray(catalog?.providers) ? catalog.providers : [];
    const result: any[] = [];
    for (const block of blocks) {
      const provider = String(block?.provider || '').trim();
      if (!provider) continue;
      const models = Array.isArray(block?.models) ? block.models : [];
      for (const m of models) {
        const modelId = String(m?.id || '').trim();
        if (!modelId) continue;
        result.push({
          id: `${provider}:${modelId}`,
          name: `${provider} (${modelId})`,
          selected: !!m?.selected,
        });
      }
    }
    return result;
  }

  private loadProvidersFallback() {
    if (!this.hub) return;
    this.taskFacade.listProviders(this.hub.url).subscribe({
      next: (providers) => {
        this.availableProviders = providers;
      },
      error: () => {
        console.warn('Providers konnten nicht geladen werden, verwende Fallback');
        this.availableProviders = [
          { id: 'ollama:llama3', name: 'Ollama (Llama3)', selected: true },
          { id: 'openai:gpt-4o', name: 'OpenAI (GPT-4o)', selected: false }
        ];
      }
    });
  }

  loadInstructionOptions() {
    if (!this.hub) return;
    this.taskFacade.listInstructionProfiles(this.hub.url).subscribe({
      next: (profiles: any[]) => {
        this.instructionProfiles = Array.isArray(profiles) ? profiles : [];
        this.ensureInstructionSelections();
      },
      error: () => this.ns.error('Instruction-Profile konnten nicht geladen werden')
    });
    this.taskFacade.listInstructionOverlays(this.hub.url).subscribe({
      next: (overlays: any[]) => {
        this.instructionOverlays = Array.isArray(overlays) ? overlays : [];
        this.ensureInstructionSelections();
      },
      error: () => this.ns.error('Instruction-Overlays konnten nicht geladen werden')
    });
  }

  private ensureInstructionSelections() {
    const profileIds = new Set(this.instructionProfiles.map(item => item.id));
    const overlayIds = new Set(this.instructionOverlays.map(item => item.id));
    if (this.instructionProfileSelectionId && !profileIds.has(this.instructionProfileSelectionId)) this.instructionProfileSelectionId = '';
    if (this.instructionOverlaySelectionId && !overlayIds.has(this.instructionOverlaySelectionId)) this.instructionOverlaySelectionId = '';
    if (this.instructionSessionOverlayId && !overlayIds.has(this.instructionSessionOverlayId)) this.instructionSessionOverlayId = '';
  }

  saveInstructionSelection() {
    if (!this.hub) return;
    this.instructionSelectionBusy = true;
    this.taskFacade.setTaskInstructionSelection(this.hub.url, this.tid, {
      profile_id: this.instructionProfileSelectionId || null,
      overlay_id: this.instructionOverlaySelectionId || null,
    }).pipe(
      finalize(() => this.instructionSelectionBusy = false)
    ).subscribe({
      next: (summary) => {
        this.task = { ...(this.task || {}), instruction_layers: summary };
        this.ns.success('Instruction-Auswahl gespeichert');
        this.refreshInstructionCompatibility();
      },
      error: (err) => this.ns.error(this.ns.fromApiError(err, 'Instruction-Auswahl konnte nicht gespeichert werden'))
    });
  }

  attachOverlayToSession() {
    if (!this.hub || !this.instructionSessionOverlayId || !this.instructionSessionId) return;
    this.instructionSelectionBusy = true;
    this.taskFacade.attachInstructionOverlay(
      this.hub.url,
      this.instructionSessionOverlayId,
      { attachment_kind: 'session', attachment_id: this.instructionSessionId }
    ).pipe(
      finalize(() => this.instructionSelectionBusy = false)
    ).subscribe({
      next: () => {
        this.ns.success('Overlay an Session gebunden');
        this.loadInstructionOptions();
      },
      error: (err) => this.ns.error(this.ns.fromApiError(err, 'Session-Bindung fehlgeschlagen'))
    });
  }

  refreshInstructionCompatibility() {
    if (!this.hub || !this.tid) return;
    this.taskFacade.getInstructionLayersEffective(this.hub.url, {
      task_id: this.tid,
      profile_id: this.instructionProfileSelectionId || undefined,
      overlay_id: this.instructionOverlaySelectionId || undefined,
      base_prompt: 'task-detail-compatibility-preview',
    }).subscribe({
      next: (payload: any) => {
        this.instructionCompatibility = payload?.diagnostics?.template_compatibility || null;
      },
      error: () => {
        this.instructionCompatibility = null;
      }
    });
  }

  get tid(){ return this.route.snapshot.paramMap.get('id')!; }

  setTab(tab: string) {
    this.activeTab = tab;
    if (tab === 'logs') {
      this.startStreaming();
    } else {
      this.stopStreaming();
    }
  }

  reload(){
    if(!this.hub) return;
    this.loadingTask = true;
    this.taskFacade.getTask(this.hub.url, this.tid).subscribe({
      next: t => {
        this.task = t;
        this.assignUrl = t?.assignment?.agent_url;
        this.instructionProfileSelectionId = String(t?.instruction_layers?.profile_id || '');
        this.instructionOverlaySelectionId = String(t?.instruction_layers?.overlay_id || '');
        if (!this.proposedTouched) {
          this.proposed = t?.last_proposal?.command || '';
          this.toolCalls = t?.last_proposal?.tool_calls || [];
        }
        this.comparisons = t?.last_proposal?.comparisons || null;
        this.refreshInstructionCompatibility();
        this.loadSourceGroundingDetails();
        if (this.activeTab === 'logs' && !this.activeLogTaskId) this.startStreaming();
        this.loadSubtasks();
      },
      error: () => {
        this.ns.error('Task konnte nicht geladen werden');
      },
      complete: () => {
        this.loadingTask = false;
      }
    });
  }

  loadSubtasks() {
    if (!this.hub) return;
    const cachedSubtasks = this.taskFacade.childrenOf(this.tid);
    if (cachedSubtasks.length) {
      this.subtasks = cachedSubtasks;
      return;
    }
    this.taskFacade.listTasks(this.hub.url).subscribe({
      next: (tasks: any) => {
        if (Array.isArray(tasks)) {
          this.subtasks = tasks.filter(t => t.parent_task_id === this.tid);
        }
      }
    });
  }

  private loadSourceGroundingDetails() {
    if (!this.hub || !this.tid) return;
    this.taskSourcesPayload = null;
    this.taskAnswerVerificationPayload = null;
    this.taskFacade.getTaskSources(this.hub.url, this.tid).subscribe({
      next: (res: any) => {
        this.taskSourcesPayload = res?.data || null;
      },
      error: () => {
        this.taskSourcesPayload = null;
      }
    });
    this.taskFacade.getTaskAnswerVerification(this.hub.url, this.tid).subscribe({
      next: (res: any) => {
        this.taskAnswerVerificationPayload = res?.data || null;
      },
      error: () => {
        this.taskAnswerVerificationPayload = null;
      }
    });
  }

  startStreaming() {
    if(!this.hub) return;
    this.stopStreaming();
    this.activeLogTaskId = this.tid;
    this.logs = [];
    this.loadingLogs = true;
    this.taskFacade.watchTaskLogs(this.hub.url, this.tid, {
      reset: true,
      onEvent: (log) => {
        const state = this.taskFacade.taskLogState(this.tid);
        this.logs = state.logs;
        this.loadingLogs = state.loading;
        if (this.taskFacade.shouldRefreshTask(log)) {
          this.reload();
        }
      },
      onError: (err) => {
        console.error('SSE Error', err);
        this.ns.error('Live-Logs Verbindung verloren');
        this.loadingLogs = false;
      }
    });
  }

  stopStreaming() {
    this.taskFacade.stopTaskLogs(this.activeLogTaskId);
    this.activeLogTaskId = undefined;
  }

  loadLogs(){
    // Veraltet, wird durch startStreaming() ersetzt, aber wir behalten es falls manuell aufgerufen
    if(!this.hub) return;
    this.loadingLogs = true;
    this.taskFacade.taskLogs(this.hub.url, this.tid).subscribe({
      next: r => this.logs = Array.isArray(r) ? r : [],
      error: () => this.ns.error('Logs konnten nicht geladen werden'),
      complete: () => { this.loadingLogs = false; }
    });
  }

  reviewProposal(action: 'approve' | 'reject') {
    if (!this.hub) return;
    this.busy = true;
    this.taskFacade.reviewTaskProposal(this.hub.url, this.tid, { action }).pipe(
      finalize(() => this.busy = false)
    ).subscribe({
      next: () => {
        this.ns.success(action === 'approve' ? 'Vorschlag freigegeben' : 'Vorschlag abgelehnt');
        this.reload();
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Review-Aktion fehlgeschlagen'))
    });
  }

  saveStatus(newStatus?: string){
    if(!this.hub || !this.task) return;
    const status = newStatus || this.task.status;
    this.taskFacade.patchTask(this.hub.url, this.tid, { status }).subscribe({
      next: () => {
        this.ns.success(`Status auf ${status} aktualisiert`);
        this.reload();
      },
      error: () => this.ns.error('Status-Update fehlgeschlagen')
    });
  }
  saveAssign(){
    if(!this.hub) return;
    const sel = this.allAgents.find(a => a.url === this.assignUrl);
    this.taskFacade.assignTask(this.hub.url, this.tid, { agent_url: this.assignUrl, token: sel?.token }).subscribe({
      next: () => {
        this.ns.success(this.assignUrl ? 'Agent zugewiesen' : 'Zuweisung aufgehoben');
        this.reload();
      },
      error: () => this.ns.error('Zuweisung fehlgeschlagen')
    });
  }
  propose(multi: boolean = false){
    if(!this.hub) return;
    this.busy = true;
    const body: any = { prompt: this.prompt };
    if (multi) {
      body.providers = this.availableProviders.filter(p => p.selected).map(p => p.id);
      if (body.providers.length === 0) {
        // Fallback falls nichts ausgewählt
        body.providers = ['ollama:llama3', 'openai:gpt-4o'];
      }
    }
    this.taskFacade.proposeTask(this.hub.url, this.tid, body).pipe(
      finalize(() => this.busy = false)
    ).subscribe({
      next: (r:any) => {
        this.proposed = r?.command || '';
        this.toolCalls = r?.tool_calls || [];
        this.proposedTouched = false;
        this.comparisons = r?.comparisons || null;
        this.ns.success('Vorschlag erhalten');
      },
      error: () => {
        this.ns.error('Fehler beim Abrufen des Vorschlags');
      }
    });
  }
  execute(){
    if(!this.hub || (!this.proposed && !this.toolCalls.length)) return;
    this.busy = true;
    this.taskFacade.executeTask(this.hub.url, this.tid, {
      command: this.proposed,
      tool_calls: this.toolCalls
    }).pipe(
      finalize(() => this.busy = false)
    ).subscribe({
      next: (r: any) => {
        this.ns.success('Befehl ausgeführt');
        this.proposed = '';
        this.proposedTouched = false;
        this.toolCalls = [];
        this.loadLogs();
      },
      error: () => {
        this.ns.error('Ausführung fehlgeschlagen');
      }
    });
  }

  useComparison(val: any) {
    this.proposed = val.command || '';
    this.toolCalls = val.tool_calls || [];
    this.proposedTouched = false;
    this.ns.success('Vorschlag übernommen');
  }

  canExecute(): boolean {
    if (this.busy) return false;
    const hasCommand = !!(this.proposed && this.proposed.trim().length > 0);
    const hasTools = !!(this.toolCalls && this.toolCalls.length > 0);

    // Debugging falls es wieder passiert
    if (!hasCommand && !hasTools && this.proposedTouched) {
      // User hat etwas getippt, aber es ist leer -> disabled ist korrekt.
    }

    return hasCommand || hasTools;
  }

  onProposedChange(value: string) {
    this.proposed = value;
    this.proposedTouched = true;
  }

  isDone(status: string | undefined | null): boolean {
    return isTaskDone(status);
  }

  isInProgress(status: string | undefined | null): boolean {
    return isTaskInProgress(status);
  }

  isFollowup(taskId: string): boolean {
    return taskId?.startsWith('followup-');
  }

  qualityGateReason(): string {
    const out = String(this.task?.last_output || '');
    const marker = '[quality_gate] failed:';
    const idx = out.indexOf(marker);
    if (idx < 0) return '';
    return out.slice(idx + marker.length).trim();
  }

  reviewState(): any {
    return this.task?.last_proposal?.review || null;
  }

  workerContextText(): string {
    return String(this.task?.worker_execution_context?.context?.context_text || '').trim();
  }

  allowedTools(): string[] {
    const tools = this.task?.worker_execution_context?.allowed_tools;
    return Array.isArray(tools) ? tools : [];
  }

  expectedSchema(): any {
    const schema = this.task?.worker_execution_context?.expected_output_schema;
    if (!schema || typeof schema !== 'object' || !Object.keys(schema).length) return null;
    return schema;
  }

  routingDecision(): any {
    const routing = this.task?.worker_execution_context?.routing;
    if (!routing || typeof routing !== 'object') return null;
    return routing;
  }

  routingRequiredCapabilities(): string[] {
    const caps = this.routingDecision()?.required_capabilities;
    return Array.isArray(caps) ? caps : [];
  }

  routingMatchedCapabilities(): string[] {
    const caps = this.routingDecision()?.matched_capabilities;
    return Array.isArray(caps) ? caps : [];
  }

  researchSources(): any[] {
    const sources = this.task?.last_proposal?.research_artifact?.sources;
    return Array.isArray(sources) ? sources : [];
  }

  researchCitations(): any[] {
    const citations = this.task?.last_proposal?.research_artifact?.citations;
    return Array.isArray(citations) ? citations : [];
  }

  researchVerification(): any {
    const verification = this.task?.last_proposal?.research_artifact?.verification;
    if (!verification || typeof verification !== 'object' || !Object.keys(verification).length) return null;
    return verification;
  }

  researchBackendMetadata(): any {
    const metadata = this.task?.last_proposal?.research_artifact?.backend_metadata;
    if (!metadata || typeof metadata !== 'object' || !Object.keys(metadata).length) return null;
    return metadata;
  }

  sourceCatalogEntries(): any[] {
    const sources = this.taskSourcesPayload?.sources;
    return Array.isArray(sources) ? sources : [];
  }

  sourceCatalogStatusClass(): string {
    const status = String(this.taskAnswerVerificationPayload?.status || '').trim().toLowerCase();
    if (status === 'verified') return 'success';
    if (status.startsWith('failed')) return 'danger';
    return 'warning';
  }

  sourceCatalogFailedCitationRefs(): string[] {
    const refs = new Set<string>();
    const failed = Array.isArray(this.taskAnswerVerificationPayload?.failed_claims) ? this.taskAnswerVerificationPayload.failed_claims : [];
    for (const item of failed) {
      const claimRefs = Array.isArray(item?.citation_refs) ? item.citation_refs : [];
      for (const ref of claimRefs) {
        const value = String(ref || '').trim();
        if (value) refs.add(value);
      }
    }
    return Array.from(refs);
  }

  sourceCatalogFailedReasonFor(sourceId: string): string {
    const source = String(sourceId || '').trim();
    if (!source) return '';
    const failed = Array.isArray(this.taskAnswerVerificationPayload?.failed_claims) ? this.taskAnswerVerificationPayload.failed_claims : [];
    for (const item of failed) {
      const claimRefs = Array.isArray(item?.citation_refs) ? item.citation_refs : [];
      if (claimRefs.includes(source)) {
        return String(item?.reason || 'verification_failed').trim();
      }
    }
    return '';
  }

  provenanceEvents(): any[] {
    const history = Array.isArray(this.task?.history) ? this.task.history : [];
    return history.filter((ev: any) => ['proposal_result', 'execution_result', 'proposal_review', 'task_delegated'].includes(ev?.event_type));
  }

  latestExecutionCostSummary(): any {
    const directSummary = this.task?.verification_status?.execution_cost || this.task?.cost_summary;
    if (directSummary && typeof directSummary === 'object') return directSummary;
    const history = Array.isArray(this.task?.history) ? [...this.task.history].reverse() : [];
    const executionEvent = history.find((ev: any) => ev?.cost_summary && (ev?.event_type === 'execution_result' || ev?.event_type === 'proposal_result'));
    return executionEvent?.cost_summary || null;
  }

  proposalRoutingDimensions(): any {
    const routing = this.task?.last_proposal?.routing;
    if (!routing || typeof routing !== 'object') return null;
    return routing;
  }

  effectiveExecutionRoutingSummary(): any {
    const verified = this.task?.verification_status?.execution_routing;
    if (verified && typeof verified === 'object') return verified;
    const cost = this.latestExecutionCostSummary();
    if (cost && typeof cost === 'object' && (cost.inference_provider || cost.execution_backend)) {
      return {
        inference_provider: cost.inference_provider,
        inference_model: cost.inference_model,
        execution_backend: cost.execution_backend,
      };
    }
    const proposalRouting = this.proposalRoutingDimensions();
    if (proposalRouting && typeof proposalRouting === 'object') {
      return {
        inference_provider: proposalRouting.inference_provider,
        inference_model: proposalRouting.inference_model,
        execution_backend: proposalRouting.execution_backend || proposalRouting.effective_backend,
      };
    }
    return null;
  }

  taskSessionReuseLabel(): string {
    const reused = this.task?.last_proposal?.routing?.session_reused;
    if (typeof reused === 'boolean') return reused ? 'ja' : 'nein';
    const sessionMode = String(this.task?.last_proposal?.routing?.session_mode || '').trim().toLowerCase();
    if (sessionMode === 'stateful') return 'unbekannt';
    return '—';
  }

  private normalizeAgentUrl(url: string | undefined | null): string {
    const raw = String(url || '').trim();
    if (!raw) return '';
    try {
      const parsed = new URL(raw);
      const port = parsed.port || (parsed.protocol === 'https:' ? '443' : '80');
      return `${parsed.protocol}//${parsed.hostname}:${port}`.toLowerCase();
    } catch {
      return raw.replace(/\/+$/, '').toLowerCase();
    }
  }

  private liveTerminalDisplayName(agentUrl: string): string {
    try {
      return new URL(agentUrl).hostname || agentUrl;
    } catch {
      return agentUrl;
    }
  }

  taskLiveTerminalConnection(): {
    kind: 'task';
    displayName: string;
    panelAgentName?: string;
    agentUrl: string;
    forwardParam: string;
    token?: string;
    queryParams: Record<string, string>;
  } | null {
    const agentUrl = String(
      this.task?.last_proposal?.routing?.live_terminal?.agent_url
      || this.task?.verification_status?.opencode_live_terminal?.agent_url
      || this.task?.verification_status?.cli_session?.agent_url
      || this.task?.assigned_agent_url
      || this.task?.assignment?.agent_url
      || ''
    ).trim();
    const forwardParam = String(
      this.task?.last_proposal?.routing?.live_terminal?.forward_param
      || this.task?.verification_status?.opencode_live_terminal?.forward_param
      || this.task?.verification_status?.cli_session?.forward_param
      || ''
    ).trim();
    if (!agentUrl || !forwardParam) return null;
    const normalizedAgentUrl = this.normalizeAgentUrl(agentUrl);
    const matchedAgent = this.allAgents.find((agent) => this.normalizeAgentUrl(agent.url) === normalizedAgentUrl);
    const displayName = String(matchedAgent?.name || this.liveTerminalDisplayName(agentUrl)).trim();
    return {
      kind: 'task',
      displayName,
      panelAgentName: matchedAgent?.name,
      agentUrl,
      forwardParam,
      token: matchedAgent?.token,
      queryParams: {
        tab: 'terminal',
        mode: 'interactive',
        forward_param: forwardParam,
      },
    };
  }

  taskDiagnosticTerminalConnection(): {
    kind: 'diagnostic';
    displayName: string;
    panelAgentName?: string;
    agentUrl: string;
    token?: string;
    queryParams: Record<string, string>;
  } | null {
    const taskConnection = this.taskLiveTerminalConnection();
    const agentUrl = String(
      taskConnection?.agentUrl
      || this.task?.assigned_agent_url
      || this.task?.assignment?.agent_url
      || this.task?.verification_status?.cli_session?.agent_url
      || this.task?.verification_status?.opencode_live_terminal?.agent_url
      || ''
    ).trim();
    if (!agentUrl) return null;
    const normalizedAgentUrl = this.normalizeAgentUrl(agentUrl);
    const matchedAgent = this.allAgents.find((agent) => this.normalizeAgentUrl(agent.url) === normalizedAgentUrl);
    return {
      kind: 'diagnostic',
      displayName: String(matchedAgent?.name || this.liveTerminalDisplayName(agentUrl)).trim(),
      panelAgentName: matchedAgent?.name,
      agentUrl,
      token: matchedAgent?.token,
      queryParams: {
        tab: 'terminal',
        mode: 'interactive',
      },
    };
  }

  selectedTaskTerminalConnection(): {
    kind: 'task' | 'diagnostic';
    displayName: string;
    panelAgentName?: string;
    agentUrl: string;
    forwardParam?: string;
    token?: string;
    queryParams: Record<string, string>;
  } | null {
    const taskConnection = this.taskLiveTerminalConnection();
    const diagnosticConnection = this.taskDiagnosticTerminalConnection();
    if (this.taskTerminalMode === 'diagnostic' && diagnosticConnection) {
      return diagnosticConnection;
    }
    return taskConnection || diagnosticConnection;
  }

  taskLiveTerminalLink(): { agentName: string; queryParams: Record<string, string> } | null {
    const connection = this.selectedTaskTerminalConnection();
    if (!connection?.panelAgentName) return null;
    return {
      agentName: connection.panelAgentName,
      queryParams: connection.queryParams,
    };
  }

  term = userFacingTerm;
  decisionExplanation = decisionExplanation;
  safetyBoundaryExplanation = safetyBoundaryExplanation;

  statusExplanation(status?: string | null): string {
    const normalized = String(status || '').trim().toLowerCase();
    if (normalized === 'blocked') return safetyBoundaryExplanation('blocked');
    if (normalized === 'failed') return safetyBoundaryExplanation('failed');
    if (normalized === 'completed') return 'Die Aufgabe ist abgeschlossen. Pruefe Ergebnisse und Artefakte, falls du sie weiterverwenden willst.';
    if (normalized === 'in_progress') return 'Ein Worker bearbeitet diese Aufgabe oder sie wartet auf den naechsten Ausfuehrungsschritt.';
    return 'Noch nicht gestartet. Der Hub kann die Aufgabe einem passenden Worker zuweisen.';
  }

  taskSafetyNotice(): { title: string; body: string } | null {
    const status = String(this.task?.status || '').trim().toLowerCase();
    if (status === 'blocked') {
      return { title: userFacingTerm('blocked').label, body: safetyBoundaryExplanation('blocked') };
    }
    if (status === 'failed') {
      return { title: 'Ausfuehrung gestoppt', body: safetyBoundaryExplanation('failed') };
    }
    if (this.reviewState()?.required) {
      return { title: 'Freigabe erforderlich', body: safetyBoundaryExplanation('pending_review') };
    }
    if (this.qualityGateReason()) {
      return { title: 'Pruefung hat angehalten', body: this.qualityGateReason() };
    }
    return null;
  }

  taskNextSteps(): NextStepAction[] {
    const status = String(this.task?.status || '').trim().toLowerCase();
    const reviewRequired = Boolean(this.reviewState()?.required);

    // Keep steps concrete and navigable. There is no dedicated /timeline route; the timeline lives on /dashboard.
    if (status === 'blocked') {
      return [
        { id: 'open-board', label: 'Board oeffnen', description: 'Blockierte Tasks gesammelt sichten.', routerLink: ['/board'] },
        { id: 'open-dashboard', label: 'Dashboard oeffnen', description: 'Timeline/Guardrails und Zusammenfassungen ansehen.', routerLink: ['/dashboard'] },
        { id: 'open-settings', label: 'Policies/Profiles pruefen', description: 'Governance Mode und Runtime Profile abgleichen.', routerLink: ['/settings'] },
      ];
    }

    if (reviewRequired) {
      return [
        { id: 'open-settings', label: 'Policies/Profiles pruefen', description: 'Review-Pflichten und Grenzen nachvollziehen.', routerLink: ['/settings'] },
        { id: 'open-dashboard', label: 'Dashboard oeffnen', description: 'Goal/Timeline Kontext ansehen.', routerLink: ['/dashboard'] },
        { id: 'open-board', label: 'Board oeffnen', description: 'Abhaengigkeiten und Status anderer Tasks pruefen.', routerLink: ['/board'] },
      ];
    }

    if (status === 'failed') {
      return [
        { id: 'open-dashboard', label: 'Dashboard oeffnen', description: 'Gesamtstatus und Timeline-Kontext ansehen.', routerLink: ['/dashboard'] },
        { id: 'open-board', label: 'Board oeffnen', description: 'Prioritaeten und Folgearbeiten planen.', routerLink: ['/board'] },
      ];
    }

    return [];
  }

  reviewNextSteps(): NextStepAction[] {
    const reviewRequired = Boolean(this.reviewState()?.required);
    if (!reviewRequired) return [];
    return [
      { id: 'open-settings', label: 'Governance Mode pruefen', description: 'Safe/Balanced/Strict Entscheidung verifizieren.', routerLink: ['/settings'] },
      { id: 'open-board', label: 'Board oeffnen', description: 'Review-required Tasks zusammen pruefen.', routerLink: ['/board'] },
    ];
  }

  isHintVisible(key: string): boolean {
    return !this.hiddenHints.has(key);
  }

  dismissHint(key: string): void {
    this.hiddenHints.add(key);
    localStorage.setItem('ananta.hidden-hints', Array.from(this.hiddenHints).join(','));
  }
}
