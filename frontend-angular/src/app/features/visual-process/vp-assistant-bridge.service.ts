import { HttpErrorResponse } from '@angular/common/http';
import { Injectable, InjectionToken, OnDestroy, computed, inject, signal } from '@angular/core';
import { Observable, Subscription } from 'rxjs';

import { SnakeEventsService } from '../../services/snake-events.service';
import { SnakeGuideService } from '../../services/snake-guide.service';
import {
  VP_ASSISTANT_ACTIVE_STATUSES,
  VP_ASSISTANT_API_PORT,
  VP_ASSISTANT_RETRYABLE_STATUSES,
  VpAssistantContextCreateRequest,
  VpAssistantCapabilities,
  VpAssistantContextResource,
  VpAssistantConversationResource,
  VpAssistantPatchPreview,
  VpAssistantRequestResource,
  VpAssistantRequestStatus,
} from './vp-assistant-api.service';
import { VpAssistantContextService, canonicalVpJson, canvasTargetToAssistantLocation } from './vp-assistant-context.service';
import { CanvasHitTarget, VpEditorContextEnvelope, VpHelpResponse, VpWorkflowPatch } from './vp-editor-context.models';
import { ValidationIssue, VpGraph, VpRuntimeOverlay } from './visual-process-api.service';
import { VpNodeDefinition } from './vp-node-definition-registry.service';

export type VpAssistantUiStatus =
  | 'idle'
  | 'creating_context'
  | 'creating_conversation'
  | 'awaiting_context_confirmation'
  | 'submitting'
  | 'error'
  | VpAssistantRequestStatus;

export type VpAssistantPatchUiStatus = 'idle' | 'loading' | 'ready' | 'accepting' | 'rejected' | 'applied' | 'conflict' | 'error';

export type VpAssistantOutcomeState = 'current' | 'stale' | 'conflict' | 'rejected' | 'no_results' | 'timeout' | 'cancelled' | 'error';

export interface VpAssistantOutcomePresentation {
  state: VpAssistantOutcomeState;
  label: string;
  detail: string;
}

/** Stable user-facing projection of Hub statuses and additive backend error codes. */
export function vpAssistantOutcomePresentation(
  status: VpAssistantUiStatus,
  errorCode: string | null | undefined,
): VpAssistantOutcomePresentation {
  const code = String(errorCode ?? '').trim().toLocaleLowerCase('en-US');
  const contains = (...needles: string[]) => needles.some(needle => code.includes(needle));
  if (contains('stale', 'outdated', 'revision_expired')) return {
    state: 'stale', label: 'Kontext ist veraltet',
    detail: 'Repository-, Index- oder Graphstand hat sich geändert. Bitte den Kontext aktualisieren.',
  };
  if (contains('conflict', 'revision_mismatch', 'context_changed')) return {
    state: 'conflict', label: 'Kontextkonflikt',
    detail: 'Die Antwort gehört nicht mehr eindeutig zum aktuellen Editorstand. Es wird kein Patch angeboten.',
  };
  if (contains('no_results', 'no_result', 'no_evidence', 'evidence_missing')) return {
    state: 'no_results', label: 'Keine belegbaren Ergebnisse',
    detail: 'Die freigegebenen Quellen liefern für diese Frage keine verifizierbare Antwort.',
  };
  if (status === 'rejected' || contains('rejected', 'policy_denied', 'not_allowed')) return {
    state: 'rejected', label: 'Anfrage abgelehnt',
    detail: 'Hub-Policy oder Evidence-Governance hat die Anfrage abgelehnt.',
  };
  if (status === 'timeout' || contains('timeout', 'timed_out')) return {
    state: 'timeout', label: 'Zeitlimit überschritten',
    detail: 'Die Anfrage wurde innerhalb des erlaubten Zeitbudgets nicht abgeschlossen.',
  };
  if (status === 'cancelled' || contains('cancelled', 'canceled')) return {
    state: 'cancelled', label: 'Anfrage abgebrochen',
    detail: 'Die laufende Assistant-Anfrage wurde beendet; der Editorstand blieb unverändert.',
  };
  if (status === 'failed' || status === 'error') return {
    state: 'error', label: 'Assistant-Anfrage fehlgeschlagen',
    detail: 'Der Hub konnte die Anfrage nicht erfolgreich abschließen.',
  };
  const labels: Partial<Record<VpAssistantUiStatus, string>> = {
    idle: 'Bereit', creating_context: 'Kontext wird erstellt …', creating_conversation: 'Unterhaltung wird erstellt …',
    awaiting_context_confirmation: 'Kontextwechsel wartet auf Bestätigung', submitting: 'Frage wird gesendet …',
    queued_retrieval: 'Quellensuche eingeplant …', retrieving: 'Quellen werden geprüft …',
    queued_inference: 'Antwort eingeplant …', inferencing: 'Antwort wird erstellt …', completed: 'Antwort vollständig',
  };
  return { state: 'current', label: labels[status] ?? 'Bereit', detail: '' };
}

export interface VpAssistantShowOptions {
  graph: VpGraph;
  target: CanvasHitTarget | null;
  definition?: VpNodeDefinition | null;
  validationIssues?: readonly ValidationIssue[];
  runtime?: VpRuntimeOverlay | null;
  editorMode: 'embedded-edit' | 'full-editor' | 'compact-readonly';
  detailLevel?: 'preview' | 'selected' | 'conversation';
  repositoryRevision?: string;
  codecompassManifestHash?: string;
  sourceAllowlistVersion?: string;
  promptVersion?: string;
}

interface PendingPatchRefresh {
  requestId: string | null;
  draftFingerprint: string;
  currentGraph: () => VpGraph;
  options: VpAssistantShowOptions;
}

export const VP_ASSISTANT_POLL_INTERVAL_MS = new InjectionToken<number>('VP_ASSISTANT_POLL_INTERVAL_MS', {
  providedIn: 'root', factory: () => 1_000,
});

export const VP_ASSISTANT_HOVER_DELAY_MS = new InjectionToken<number>('VP_ASSISTANT_HOVER_DELAY_MS', {
  providedIn: 'root', factory: () => 350,
});

@Injectable()
export class VpAssistantBridgeService implements OnDestroy {
  private readonly contextAssembler = inject(VpAssistantContextService);
  private readonly api = inject(VP_ASSISTANT_API_PORT);
  private readonly snakeGuide = inject(SnakeGuideService);
  private readonly snakeEvents = inject(SnakeEventsService);
  private readonly pollIntervalMs = inject(VP_ASSISTANT_POLL_INTERVAL_MS);
  private readonly hoverDelayMs = inject(VP_ASSISTANT_HOVER_DELAY_MS);
  private previewSuppressed = false;
  private readonly subscriptions = new Subscription();
  private previewTimer: ReturnType<typeof setTimeout> | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private requestSubscription: Subscription | null = null;
  private requestSequence = 0;
  private currentOptions: VpAssistantShowOptions | null = null;
  private remoteContextLocalId: string | null = null;
  private pendingQuestion: string | null = null;
  private previewBaseDraftFingerprint: string | null = null;
  private pendingPatchRefresh: PendingPatchRefresh | null = null;
  private readonly restoreAttemptedGraphs = new Set<string>();

  readonly visible = signal(false);
  readonly capabilities = signal<VpAssistantCapabilities>({
    contract_version: 'ananta.visual_process.assistant.capabilities.v1',
    registry_inspector: false,
    hover_help: false,
    assistant_chat: false,
    ai_patches: false,
    limits: {},
  });
  readonly pinned = signal(false);
  readonly mode = signal<'compact' | 'expanded' | 'pinned'>('compact');
  readonly target = signal<CanvasHitTarget | null>(null);
  readonly context = signal<VpEditorContextEnvelope | null>(null);
  readonly response = signal<VpHelpResponse | null>(null);
  readonly requestStatus = signal<VpAssistantUiStatus>('idle');
  readonly errorCode = signal<string | null>(null);
  readonly conversation = signal<VpAssistantConversationResource | null>(null);
  readonly remoteContext = signal<VpAssistantContextResource | null>(null);
  readonly pendingContext = signal<VpAssistantContextResource | null>(null);
  readonly activeRequest = signal<VpAssistantRequestResource | null>(null);
  readonly patchPreview = signal<VpAssistantPatchPreview | null>(null);
  readonly patchProposal = signal<VpWorkflowPatch | null>(null);
  readonly patchRequestId = signal<string | null>(null);
  readonly patchBaseGraph = signal<VpGraph | null>(null);
  readonly patchStatus = signal<VpAssistantPatchUiStatus>('idle');
  readonly patchError = signal<string | null>(null);
  readonly awaitingReply = computed(() => {
    const status = this.requestStatus();
    return status === 'creating_context' || status === 'creating_conversation' || status === 'submitting'
      || VP_ASSISTANT_ACTIVE_STATUSES.has(status as VpAssistantRequestStatus);
  });
  readonly canCancel = computed(() => {
    const request = this.activeRequest();
    return !!request && VP_ASSISTANT_ACTIVE_STATUSES.has(request.status);
  });
  readonly canRetry = computed(() => {
    const request = this.activeRequest();
    return !!request && VP_ASSISTANT_RETRYABLE_STATUSES.has(request.status);
  });
  readonly contextSwitchPending = computed(() => this.requestStatus() === 'awaiting_context_confirmation');
  readonly outcome = computed(() => vpAssistantOutcomePresentation(this.requestStatus(), this.errorCode()));
  readonly patchAllowed = computed(() => {
    const request = this.activeRequest();
    return request?.status === 'completed'
      && this.outcome().state === 'current'
      && this.currentOptions?.editorMode !== 'compact-readonly'
      && this.capabilities().ai_patches
      && !!request.response?.workflow_patch;
  });

  constructor() {
    this.subscriptions.add(this.api.capabilities().subscribe({
      next: capabilities => this.capabilities.set(capabilities),
      error: () => { /* fail-closed defaults remain active */ },
    }));
    this.subscriptions.add(this.snakeGuide.play$.subscribe(steps => {
      const first = steps[0];
      if (!first || !this.visible() || this.activeRequest()?.status === 'completed') return;
      this.response.update(current => ({
        summary: first.bubble,
        location: current?.location ?? this.target()?.role ?? 'Workflow',
        explanation: first.bubble,
        options: current?.options ?? [],
        warnings: current?.warnings ?? [],
        next_actions: steps.slice(1, 4).map(step => step.bubble),
        evidence: current?.evidence ?? [],
        context_id: this.context()?.context_id,
      }));
    }));
    this.subscriptions.add(this.snakeEvents.guide$.subscribe(payload => {
      const first = payload.steps?.[0];
      if (!first || !this.visible() || this.activeRequest()?.status === 'completed') return;
      this.response.update(current => ({
        summary: first.bubble,
        location: current?.location ?? this.target()?.role ?? 'Workflow',
        explanation: first.bubble,
        options: current?.options ?? [],
        warnings: current?.warnings ?? [],
        next_actions: payload.steps.slice(1, 4).map(step => step.bubble),
        evidence: current?.evidence ?? [],
        context_id: this.context()?.context_id,
      }));
    }));
  }

  preview(options: VpAssistantShowOptions): void {
    // An explicit selection owns the expanded context. Hover must never replace
    // it; a different target is selected explicitly through select().
    if (
      this.previewSuppressed
      || !this.capabilities().hover_help
      || this.mode() !== 'compact'
      || this.awaitingReply()
    ) return;
    this.clearPreviewTimer();
    if (!options.target) {
      if (this.mode() === 'compact') this.visible.set(false);
      return;
    }
    this.previewTimer = setTimeout(
      () => void this.show({ ...options, detailLevel: 'preview' }),
      Math.max(0, this.hoverDelayMs),
    );
  }

  setPreviewSuppressed(suppressed: boolean): void {
    this.previewSuppressed = suppressed;
    if (suppressed) this.clearPreviewTimer();
  }

  select(options: VpAssistantShowOptions): void {
    if ((!this.capabilities().hover_help && !this.capabilities().assistant_chat) || this.awaitingReply()) return;
    this.clearPreviewTimer();
    void this.show({ ...options, detailLevel: 'selected' });
    this.mode.set('expanded');
  }

  async show(options: VpAssistantShowOptions): Promise<void> {
    const target = options.target;
    if (!target) return;
    if (this.target()?.entityId !== target.entityId) this.clearPatchPreview();
    this.currentOptions = this.snapshotOptions(options);
    const sequence = ++this.requestSequence;
    this.target.set(target);
    this.visible.set(true);
    this.response.set(this.localResponse(options));
    try {
      const context = await this.contextAssembler.assemble({
        graph: options.graph,
        target,
        detailLevel: options.detailLevel ?? 'preview',
        editorMode: options.editorMode,
        runtime: options.runtime,
        validationIssues: options.validationIssues,
        repositoryRevision: options.repositoryRevision,
        codecompassManifestHash: options.codecompassManifestHash,
        sourceAllowlistVersion: options.sourceAllowlistVersion,
        promptVersion: options.promptVersion,
      });
      if (sequence !== this.requestSequence) return;
      this.context.set(context);
      this.response.update(response => response ? { ...response, context_id: context.context_id } : response);
    } catch {
      // The registry-driven local help remains usable without WebCrypto.
      if (sequence === this.requestSequence) this.context.set(null);
    }
  }

  setPinned(pinned: boolean): void {
    this.clearPreviewTimer();
    this.pinned.set(pinned);
    this.mode.set(pinned ? 'pinned' : 'expanded');
  }

  setMode(mode: 'compact' | 'expanded' | 'pinned'): void {
    this.mode.set(mode);
    this.pinned.set(mode === 'pinned');
  }

  close(): void {
    this.clearPreviewTimer();
    this.visible.set(false);
    this.pinned.set(false);
    this.mode.set('compact');
  }

  ask(question: string): boolean {
    const text = question.trim();
    const options = this.currentOptions;
    if (!this.capabilities().assistant_chat || !text || !options?.target || this.awaitingReply()) return false;
    this.mode.set(this.pinned() ? 'pinned' : 'expanded');
    this.pendingQuestion = text;
    void this.beginQuestion(options, text);
    return true;
  }

  confirmContextSwitch(): void {
    const conversation = this.conversation();
    const context = this.pendingContext();
    const question = this.pendingQuestion;
    if (!conversation || !context || !question) return;
    this.errorCode.set(null);
    this.requestStatus.set('submitting');
    this.runRequest(this.api.switchConversationContext(conversation.conversation_id, context.context_id), {
      next: switched => {
        this.conversation.set(switched);
        this.storeConversation(switched);
        this.remoteContext.set(context);
        this.pendingContext.set(null);
        this.submitQuestion(switched, question);
      },
      error: error => this.fail(error),
    });
  }

  rejectContextSwitch(): void {
    this.pendingContext.set(null);
    this.pendingQuestion = null;
    this.requestStatus.set('idle');
    this.errorCode.set(null);
  }

  cancelRequest(): void {
    const request = this.activeRequest();
    if (!request || !VP_ASSISTANT_ACTIVE_STATUSES.has(request.status)) return;
    this.clearPollTimer();
    this.runRequest(this.api.cancelRequest(request.request_id), {
      next: cancelled => this.applyRequest(cancelled),
      error: error => this.fail(error),
    });
  }

  retryRequest(): void {
    const request = this.activeRequest();
    if (!request || !VP_ASSISTANT_RETRYABLE_STATUSES.has(request.status)) return;
    this.errorCode.set(null);
    this.requestStatus.set('submitting');
    this.runRequest(this.api.retryRequest(
      request.request_id,
      this.uniqueId('vp-client'),
      this.uniqueId('vp-idempotency'),
    ), {
      next: retried => this.applyRequest(retried),
      error: error => this.fail(error),
    });
  }

  previewWorkflowPatch(currentGraph: VpGraph): void {
    const request = this.activeRequest();
    const patch = request?.response?.workflow_patch;
    const frozenGraph = this.currentOptions?.graph;
    if (!this.patchAllowed() || !request || !patch || !frozenGraph) return;
    if (!this.sameDraft(currentGraph, frozenGraph)) {
      this.patchStatus.set('conflict');
      this.patchError.set('assistant_patch_draft_changed_since_question');
      return;
    }
    this.patchStatus.set('loading');
    this.patchError.set(null);
    this.previewBaseDraftFingerprint = canonicalVpJson(currentGraph);
    this.patchBaseGraph.set(this.clone(currentGraph));
    this.patchProposal.set(this.clone(patch));
    this.patchRequestId.set(request.request_id);
    this.runRequest(this.api.previewPatch(request.request_id, patch, this.contextDraftGraph(currentGraph)), {
      next: preview => {
        this.patchPreview.set(preview);
        this.patchStatus.set('ready');
      },
      error: error => this.failPatch(error),
    });
  }

  acceptWorkflowPatch(currentGraph: () => VpGraph, apply: (preview: VpAssistantPatchPreview) => boolean): void {
    const request = this.activeRequest();
    const preview = this.patchPreview();
    const previewRequestId = this.patchRequestId();
    if (!this.patchAllowed() || !request || !preview || !previewRequestId || this.patchStatus() !== 'ready') return;
    if (this.previewBaseDraftFingerprint !== canonicalVpJson(currentGraph())) {
      this.patchStatus.set('conflict');
      this.patchError.set('assistant_patch_draft_changed_after_preview');
      return;
    }
    this.patchStatus.set('accepting');
    this.runRequest(this.api.decidePatch(
      previewRequestId,
      preview.patch_hash,
      'accepted',
      true,
      this.contextDraftGraph(currentGraph()),
    ), {
      next: () => {
        if (this.previewBaseDraftFingerprint !== canonicalVpJson(currentGraph()) || !apply(preview)) {
          this.patchStatus.set('conflict');
          this.patchError.set('assistant_patch_local_apply_conflict');
          return;
        }
        this.patchStatus.set('applied');
        this.patchError.set(null);
      },
      error: error => this.failPatch(error),
    });
  }

  refreshWorkflowPatch(
    currentGraph: () => VpGraph,
    validationIssues: readonly ValidationIssue[] = [],
    runtime: VpRuntimeOverlay | null = null,
  ): void {
    const request = this.activeRequest();
    const options = this.currentOptions;
    if (
      this.patchStatus() !== 'conflict'
      || !request?.response?.workflow_patch
      || !options?.target
      || options.editorMode === 'compact-readonly'
      || !this.capabilities().ai_patches
    ) return;
    const draft = this.clone(currentGraph());
    const refreshedOptions = this.snapshotOptions({
      ...options,
      graph: draft,
      validationIssues,
      runtime,
      detailLevel: 'conversation',
    });
    const pending: PendingPatchRefresh = {
      requestId: null,
      draftFingerprint: canonicalVpJson(draft),
      currentGraph,
      options: refreshedOptions,
    };
    this.pendingPatchRefresh = pending;
    this.patchStatus.set('loading');
    this.patchError.set(null);
    this.clearPollTimer();
    this.runRequest(this.api.refreshPatch(request.request_id, {
      draft_graph: this.contextDraftGraph(draft),
      validation_issues: this.clone([...validationIssues]),
      ...(runtime ? { runtime_overlay: this.clone(runtime) } : {}),
      client_request_id: this.uniqueId('vp-client'),
    }, this.uniqueId('vp-idempotency')), {
      next: refreshed => {
        pending.requestId = refreshed.request_id;
        this.currentOptions = refreshedOptions;
        if (refreshed.refresh_context_id) {
          this.conversation.update(conversation => conversation ? {
            ...conversation,
            active_context_id: refreshed.refresh_context_id!,
            updated_at: refreshed.updated_at,
          } : conversation);
        }
        this.applyRequest(refreshed);
      },
      error: error => {
        this.pendingPatchRefresh = null;
        this.failPatch(error);
      },
    });
  }

  rejectWorkflowPatch(): void {
    const requestId = this.patchRequestId();
    const preview = this.patchPreview();
    if (!requestId || !preview) { this.clearPatchPreview(); return; }
    this.runRequest(this.api.decidePatch(requestId, preview.patch_hash, 'rejected', false), {
      next: () => {
        this.patchStatus.set('rejected');
        this.patchError.set(null);
      },
      error: error => this.failPatch(error),
    });
  }

  clearPatchPreview(): void {
    this.patchPreview.set(null);
    this.patchProposal.set(null);
    this.patchRequestId.set(null);
    this.patchBaseGraph.set(null);
    this.patchStatus.set('idle');
    this.patchError.set(null);
    this.previewBaseDraftFingerprint = null;
    this.pendingPatchRefresh = null;
  }

  ngOnDestroy(): void {
    this.clearPreviewTimer();
    this.clearPollTimer();
    this.requestSubscription?.unsubscribe();
    this.subscriptions.unsubscribe();
  }

  private async beginQuestion(options: VpAssistantShowOptions, question: string): Promise<void> {
    this.errorCode.set(null);
    this.requestStatus.set('creating_context');
    try {
      const local = await this.contextAssembler.assemble({
        graph: options.graph,
        target: options.target!,
        detailLevel: 'conversation',
        editorMode: options.editorMode,
        runtime: options.runtime,
        validationIssues: options.validationIssues,
        repositoryRevision: options.repositoryRevision,
        codecompassManifestHash: options.codecompassManifestHash,
        sourceAllowlistVersion: options.sourceAllowlistVersion,
        promptVersion: options.promptVersion,
      });
      this.context.set(local);
      if (this.remoteContextLocalId === local.context_id && this.remoteContext()) {
        this.useRemoteContext(this.remoteContext()!, question);
        return;
      }
      this.runRequest(this.api.createContext(this.contextCreateRequest(options, local)), {
        next: remote => {
          this.remoteContextLocalId = local.context_id;
          this.remoteContext.set(remote);
          this.context.set({ ...remote.context, context_id: remote.context_id, detail_level: 'conversation' });
          this.useRemoteContext(remote, question);
        },
        error: error => this.fail(error),
      });
    } catch (error) {
      this.fail(error);
    }
  }

  private useRemoteContext(context: VpAssistantContextResource, question: string): void {
    const conversation = this.conversation();
    if (!conversation || conversation.graph_id !== context.graph_id) {
      const storedConversationId = this.storedConversationId(context.graph_id);
      if (storedConversationId && !this.restoreAttemptedGraphs.has(context.graph_id)) {
        this.restoreAttemptedGraphs.add(context.graph_id);
        this.requestStatus.set('creating_conversation');
        this.runRequest(this.api.getConversation(storedConversationId), {
          next: restored => {
            if (restored.graph_id !== context.graph_id || restored.status !== 'active') {
              this.removeStoredConversation(context.graph_id);
              this.createConversation(context, question);
              return;
            }
            this.conversation.set(restored);
            this.storeConversation(restored);
            const latest = restored.requests?.at(-1);
            if (latest && VP_ASSISTANT_ACTIVE_STATUSES.has(latest.status)) {
              this.pendingQuestion = null;
              this.applyRequest(latest);
              return;
            }
            this.useExistingConversation(restored, context, question);
          },
          error: () => {
            this.removeStoredConversation(context.graph_id);
            this.createConversation(context, question);
          },
        });
        return;
      }
      this.createConversation(context, question);
      return;
    }
    this.useExistingConversation(conversation, context, question);
  }

  private createConversation(context: VpAssistantContextResource, question: string): void {
      this.requestStatus.set('creating_conversation');
      this.runRequest(this.api.createConversation(context.context_id), {
        next: created => {
          this.conversation.set(created);
          this.storeConversation(created);
          this.submitQuestion(created, question);
        },
        error: error => this.fail(error),
      });
  }

  private useExistingConversation(conversation: VpAssistantConversationResource, context: VpAssistantContextResource, question: string): void {
    if (conversation.active_context_id !== context.context_id) {
      this.pendingContext.set(context);
      this.pendingQuestion = question;
      this.requestStatus.set('awaiting_context_confirmation');
      return;
    }
    this.submitQuestion(conversation, question);
  }

  private submitQuestion(conversation: VpAssistantConversationResource, question: string): void {
    this.pendingQuestion = null;
    this.requestStatus.set('submitting');
    this.runRequest(this.api.submitQuestion(
      conversation.conversation_id,
      question,
      this.uniqueId('vp-client'),
      this.uniqueId('vp-idempotency'),
    ), {
      next: request => this.applyRequest(request),
      error: error => this.fail(error),
    });
  }

  private applyRequest(request: VpAssistantRequestResource): void {
    const pendingRefresh = this.pendingPatchRefresh;
    this.activeRequest.set(request);
    this.requestStatus.set(request.status);
    this.errorCode.set(request.error_code ?? null);
    if (vpAssistantOutcomePresentation(request.status, request.error_code).state !== 'current') this.clearPatchPreview();
    if (request.response) {
      this.response.set(request.response);
      this.context.update(context => context ? { ...context, context_id: request.response!.context_id ?? context.context_id } : context);
    }
    if (VP_ASSISTANT_ACTIVE_STATUSES.has(request.status)) this.schedulePoll(request.request_id);
    else this.clearPollTimer();

    if (!pendingRefresh || pendingRefresh.requestId !== request.request_id || request.status !== 'completed') return;
    this.pendingPatchRefresh = null;
    const current = pendingRefresh.currentGraph();
    if (canonicalVpJson(current) !== pendingRefresh.draftFingerprint) {
      this.patchStatus.set('conflict');
      this.patchError.set('assistant_patch_draft_changed_during_refresh');
      return;
    }
    if (!request.response?.workflow_patch) {
      this.patchStatus.set('error');
      this.patchError.set('assistant_patch_refresh_patch_missing');
      return;
    }
    this.previewWorkflowPatch(current);
  }

  private schedulePoll(requestId: string): void {
    this.clearPollTimer();
    this.pollTimer = setTimeout(() => {
      this.runRequest(this.api.getRequest(requestId), {
        next: request => this.applyRequest(request),
        error: error => this.fail(error),
      });
    }, this.pollIntervalMs);
  }

  private contextCreateRequest(options: VpAssistantShowOptions, local: VpEditorContextEnvelope): VpAssistantContextCreateRequest {
    return {
      graph_id: local.graph_id,
      location: canvasTargetToAssistantLocation(local.graph_id, options.target!),
      editor_mode: local.editor_mode,
      repository_revision: local.repository_revision,
      codecompass_manifest_hash: local.codecompass_manifest_hash,
      source_allowlist_version: local.source_allowlist_version,
      source_scope: this.metadataText(options.graph, 'source_scope') || 'repository',
      ...this.catalogReference(options.graph),
      draft_graph: this.contextDraftGraph(options.graph),
      ...(options.runtime ? { runtime_overlay: this.clone(options.runtime) } : {}),
      validation_issues: this.clone([...(options.validationIssues ?? [])]),
      locale: local.locale,
    };
  }

  private catalogReference(graph: VpGraph): Partial<Pick<VpAssistantContextCreateRequest, 'catalog_task_id' | 'catalog_id' | 'catalog_hash'>> {
    const nested = graph.metadata?.['source_catalog'];
    const catalog = nested && typeof nested === 'object' ? nested as Record<string, unknown> : {};
    const values = {
      catalog_task_id: this.metadataText(graph, 'catalog_task_id') || this.recordText(catalog, 'catalog_task_id'),
      catalog_id: this.metadataText(graph, 'catalog_id') || this.recordText(catalog, 'catalog_id'),
      catalog_hash: this.metadataText(graph, 'catalog_hash') || this.recordText(catalog, 'catalog_hash'),
    };
    return Object.values(values).every(Boolean) ? values : {};
  }

  private metadataText(graph: VpGraph, key: string): string {
    const value = graph.metadata?.[key];
    return typeof value === 'string' ? value.trim() : '';
  }

  private contextDraftGraph(graph: VpGraph): VpGraph {
    const draft = this.clone(graph);
    if (draft.metadata) {
      delete draft.metadata['source_refs'];
      delete draft.metadata['evidence_refs'];
    }
    return draft;
  }

  private recordText(record: Record<string, unknown>, key: string): string {
    const value = record[key];
    return typeof value === 'string' ? value.trim() : '';
  }

  private localResponse(options: VpAssistantShowOptions): VpHelpResponse {
    const target = options.target!;
    const step = target.stepId ? options.graph.steps.find(item => item.id === target.stepId) : null;
    const edge = target.edgeId ? options.graph.edges.find(item => item.id === target.edgeId) : null;
    const issues = (options.validationIssues ?? []).filter(issue => !target.stepId || issue.step_id === target.stepId);
    const runtime = target.stepId ? options.runtime?.steps[target.stepId] : null;
    const location = step ? `Node „${step.label}“` : edge ? `Kante ${edge.source} → ${edge.target}` : 'Workflow-Canvas';
    const purpose = options.definition?.purpose
      ?? (edge ? `Verbindet ${edge.source} mit ${edge.target}.` : 'Orientierung im visuellen Workflow.');
    return {
      summary: `${location}: ${purpose}`,
      location,
      explanation: purpose,
      options: options.definition?.fields.slice(0, 5).map(field => field.label) ?? [],
      warnings: [
        ...issues.map(issue => issue.message),
        ...(options.definition && !options.definition.capabilityFlags.executable ? ['Dieser Node ist derzeit nicht ausführbar.'] : []),
        ...(runtime?.error ? [runtime.error] : []),
      ],
      next_actions: step ? ['Konfiguration im Inspector prüfen', 'Inputs und Outputs verbinden'] : ['Node auswählen oder über die Palette hinzufügen'],
      evidence: [],
    };
  }

  private fail(error: unknown): void {
    this.clearPollTimer();
    this.requestStatus.set('error');
    if (error instanceof HttpErrorResponse) {
      const payload = error.error as Record<string, unknown> | null;
      this.errorCode.set(String(payload?.['error_code'] ?? payload?.['error'] ?? `http_${error.status}`));
    } else if (error instanceof Error) {
      this.errorCode.set(error.message || 'assistant_request_failed');
    } else {
      this.errorCode.set('assistant_request_failed');
    }
  }

  private failPatch(error: unknown): void {
    this.patchStatus.set(error instanceof HttpErrorResponse && error.status === 409 ? 'conflict' : 'error');
    if (error instanceof HttpErrorResponse) {
      const payload = error.error as Record<string, unknown> | null;
      this.patchError.set(String(payload?.['error_code'] ?? payload?.['error'] ?? `http_${error.status}`));
    } else {
      this.patchError.set(error instanceof Error ? error.message : 'assistant_patch_request_failed');
    }
  }

  private sameDraft(left: VpGraph, right: VpGraph): boolean {
    try { return canonicalVpJson(left) === canonicalVpJson(right); } catch { return false; }
  }

  private uniqueId(prefix: string): string {
    const uuid = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `${prefix}-${uuid}`;
  }

  private storeConversation(conversation: VpAssistantConversationResource): void {
    try { sessionStorage.setItem(this.conversationStorageKey(conversation.graph_id), conversation.conversation_id); } catch { /* storage is optional */ }
  }

  private storedConversationId(graphId: string): string | null {
    try { return sessionStorage.getItem(this.conversationStorageKey(graphId)); } catch { return null; }
  }

  private removeStoredConversation(graphId: string): void {
    try { sessionStorage.removeItem(this.conversationStorageKey(graphId)); } catch { /* storage is optional */ }
  }

  private conversationStorageKey(graphId: string): string {
    return `ananta.visual-process.assistant.conversation.v1:${graphId}`;
  }

  private snapshotOptions(options: VpAssistantShowOptions): VpAssistantShowOptions {
    return {
      ...options,
      graph: this.clone(options.graph),
      target: options.target ? { ...options.target } : null,
      validationIssues: this.clone([...(options.validationIssues ?? [])]),
      runtime: options.runtime ? this.clone(options.runtime) : options.runtime,
    };
  }

  private clone<T>(value: T): T {
    return typeof structuredClone === 'function'
      ? structuredClone(value)
      : JSON.parse(JSON.stringify(value)) as T;
  }

  private runRequest<T>(source: Observable<T>, handlers: { next: (value: T) => void; error: (error: unknown) => void }): void {
    this.requestSubscription?.unsubscribe();
    const holder = new Subscription();
    this.requestSubscription = holder;
    holder.add(source.subscribe(handlers));
  }

  private clearPreviewTimer(): void {
    if (this.previewTimer) clearTimeout(this.previewTimer);
    this.previewTimer = null;
  }

  private clearPollTimer(): void {
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollTimer = null;
  }
}
