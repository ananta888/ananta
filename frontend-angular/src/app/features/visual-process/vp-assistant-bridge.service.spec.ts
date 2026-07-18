import { TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { Subject, of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SnakeEventsService } from '../../services/snake-events.service';
import { SnakeGuideService } from '../../services/snake-guide.service';
import { VP_ASSISTANT_API_PORT, VpAssistantContextResource, VpAssistantRequestResource } from './vp-assistant-api.service';
import { VP_ASSISTANT_HOVER_DELAY_MS, VpAssistantBridgeService } from './vp-assistant-bridge.service';
import { VpAssistantContextService } from './vp-assistant-context.service';
import { VpEditorContextEnvelope } from './vp-editor-context.models';

const graph = { id: 'g', name: 'Graph', description: '', version: '1', tags: [], steps: [], edges: [] };

function context(entityId: string): VpEditorContextEnvelope {
  const suffix = entityId === 'second' ? 'b' : entityId === 'selected' ? 'c' : entityId === 'changed' ? 'd' : 'a';
  return {
    contract_version: 'ananta.visual_process.editor_context.v1', context_id: `ctx-sha256:${suffix.repeat(64)}`,
    graph_id: 'g', repository_revision: 'repo-1', codecompass_manifest_hash: 'manifest-1',
    source_allowlist_version: 'allow-1', prompt_version: 'visual-process-assistant.v1', graph_schema_version: '1',
    node_registry_version: '1', definition_revision: 1, definition_hash: 'b'.repeat(64), draft_hash: 'c'.repeat(64),
    editor_mode: 'editor', locale: 'de', location: { target_kind: 'node', graph_id: 'g', entity_id: entityId },
    graph_excerpt: {}, effective_configuration: {}, validation_issues: [], evidence_refs: [], allowed_mutations: [], extensions: {},
    detail_level: 'preview',
  };
}

function remoteContext(entityId: string): VpAssistantContextResource {
  const local = context(entityId);
  const { context_id: _localId, detail_level: _detail, ...payload } = local;
  return {
    context_id: `remote-${entityId}`, graph_id: 'g', definition_revision: 1, definition_hash: 'b'.repeat(64),
    editor_mode: 'editor', locale: 'de', context: payload, created_at: 1,
  };
}

function request(status: VpAssistantRequestResource['status'], response: VpAssistantRequestResource['response'] = null): VpAssistantRequestResource {
  return {
    request_id: 'request-1', conversation_id: 'conversation-1', context_id: 'remote-first', prompt_version: 'v1',
    client_request_id: 'client-1', status, response, created_at: 1, updated_at: 1,
  };
}

const workflowPatch = {
  contract_version: 'ananta.visual_process.workflow_patch.v1' as const, graph_id: 'g', definition_revision: 1,
  base_graph_hash: 'b'.repeat(64), evidence_refs: [], extensions: {}, operations: [{
    operation_id: 'op-1', op: 'add_step' as const, temp_id: 'new-step', value: {
      label: 'New', kind: 'review', io: { inputs: [], outputs: [] }, position: { x: 0, y: 0 }, policy_hints: [], gate: false,
    }, evidence_refs: [],
  }],
};

describe('VpAssistantBridgeService', () => {
  const guide = new Subject<Array<{ bubble: string }>>();
  const events = new Subject<{ steps: Array<{ bubble: string }> }>();
  const assemble = vi.fn(async (options: { target: { entityId: string } }) => context(options.target.entityId));
  const api = {
    capabilities: vi.fn(() => of({
      contract_version: 'ananta.visual_process.assistant.capabilities.v1', registry_inspector: true,
      hover_help: true, assistant_chat: true, ai_patches: true, limits: {},
    })),
    createContext: vi.fn((body: { location: { entity_id?: string } }) => of(remoteContext(body.location.entity_id ?? 'first'))),
    getContext: vi.fn(),
    createConversation: vi.fn((contextId: string) => of({ conversation_id: 'conversation-1', graph_id: 'g', status: 'active', active_context_id: contextId, created_at: 1, updated_at: 1 })),
    getConversation: vi.fn(),
    switchConversationContext: vi.fn((_conversationId: string, contextId: string) => of({ conversation_id: 'conversation-1', graph_id: 'g', status: 'active', active_context_id: contextId, created_at: 1, updated_at: 2 })),
    submitQuestion: vi.fn(() => of(request('queued_retrieval'))),
    getRequest: vi.fn(() => of(request('completed', {
      contract_version: 'ananta.visual_process.help_response.v1', context_id: 'remote-first', prompt_version: 'v1',
      summary: 'Hub-Antwort', location: { target_kind: 'node', graph_id: 'g', entity_id: 'first' }, explanation: 'Geerdet',
      options: [{ label: 'Option A' }], warnings: [], next_actions: [], evidence: [], claims: [], workflow_patch: null, extensions: {},
    }))),
    cancelRequest: vi.fn(() => of(request('cancelled'))),
    retryRequest: vi.fn(() => of(request('queued_retrieval'))),
    previewPatch: vi.fn(() => of({
      patch_hash: 'patch-hash', base_graph_hash: 'b'.repeat(64), preview_graph_hash: 'c'.repeat(64),
      preview_graph: { ...graph, steps: [{ id: 'new-step', label: 'New', kind: 'review', io: { inputs: [], outputs: [] }, position: { x: 0, y: 0 }, policy_hints: [], gate: false }] },
      validation: { valid: true, error_count: 0, warning_count: 0, issues: [] }, operation_count: 1,
      audit_id: 'audit-1', decision: 'previewed',
    })),
    refreshPatch: vi.fn(() => of({
      ...request('queued_retrieval'), request_id: 'request-refresh', context_id: 'remote-refresh',
      refresh_of_request_id: 'request-1', refresh_context_id: 'remote-refresh',
    })),
    decidePatch: vi.fn((_requestId: string, patchHash: string, decision: 'accepted' | 'rejected') => of({
      audit_id: 'audit-1', request_id: 'request-1', patch_hash: patchHash, decision,
      apply_mode: decision === 'accepted' ? 'local_editor_command_only' : 'none', preview: {},
    })),
  };

  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
    assemble.mockClear();
    for (const value of Object.values(api)) if ('mockClear' in value) value.mockClear();
    TestBed.configureTestingModule({ providers: [
      VpAssistantBridgeService,
      { provide: VpAssistantContextService, useValue: { assemble } },
      { provide: VP_ASSISTANT_API_PORT, useValue: api },
      { provide: SnakeGuideService, useValue: { play$: guide } },
      { provide: SnakeEventsService, useValue: { guide$: events } },
    ] });
  });

  afterEach(() => vi.useRealTimers());

  it('debounces hover for 350 ms and cancels an obsolete target', async () => {
    const service = TestBed.inject(VpAssistantBridgeService);
    const options = (entityId: string) => ({
      graph, target: { kind: 'palette_item' as const, graphId: 'g', entityId, role: 'node-template' }, editorMode: 'full-editor' as const,
    });
    service.preview(options('first'));
    await vi.advanceTimersByTimeAsync(349);
    expect(assemble).not.toHaveBeenCalled();
    service.preview(options('second'));
    await vi.advanceTimersByTimeAsync(350);
    expect(assemble).toHaveBeenCalledTimes(1);
    expect(assemble.mock.calls[0][0].target.entityId).toBe('second');
    expect(service.context()?.location.entity_id).toBe('second');
  });

  it('honors the configured hover delay and does not replace an expanded selection', async () => {
    TestBed.overrideProvider(VP_ASSISTANT_HOVER_DELAY_MS, { useValue: 25 });
    const service = TestBed.inject(VpAssistantBridgeService);
    const options = (entityId: string) => ({
      graph, target: { kind: 'node' as const, graphId: 'g', entityId, stepId: entityId, role: 'worker' },
      editorMode: 'full-editor' as const,
    });

    service.preview(options('hover'));
    await vi.advanceTimersByTimeAsync(24);
    expect(assemble).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(service.target()?.entityId).toBe('hover');

    service.select(options('selected'));
    await Promise.resolve();
    expect(service.target()?.entityId).toBe('selected');
    service.preview(options('must-not-replace-selection'));
    await vi.advanceTimersByTimeAsync(100);
    expect(service.target()?.entityId).toBe('selected');
  });

  it('cancels pending hover while modal interaction is suppressed and preserves a pinned context', async () => {
    const service = TestBed.inject(VpAssistantBridgeService);
    const options = (entityId: string) => ({
      graph,
      target: { kind: 'node' as const, graphId: 'g', entityId, stepId: entityId, role: 'worker' },
      editorMode: 'full-editor' as const,
    });
    service.preview(options('pending-hover'));
    service.setPreviewSuppressed(true);
    service.select(options('pinned'));
    await Promise.resolve();
    service.setPinned(true);

    await vi.advanceTimersByTimeAsync(500);
    expect(assemble).toHaveBeenCalledTimes(1);
    expect(service.target()?.entityId).toBe('pinned');
    expect(service.pinned()).toBe(true);

    service.setPreviewSuppressed(false);
    service.setMode('compact');
    service.preview(options('after-modal'));
    await vi.advanceTimersByTimeAsync(350);
    expect(assemble).toHaveBeenCalledTimes(2);
    expect(service.target()?.entityId).toBe('after-modal');
  });

  it('keeps hover, chat and patches fail-closed when Hub capabilities disable them', async () => {
    api.capabilities.mockReturnValueOnce(of({
      contract_version: 'ananta.visual_process.assistant.capabilities.v1', registry_inspector: false,
      hover_help: false, assistant_chat: false, ai_patches: false, limits: {},
    }));
    const service = TestBed.inject(VpAssistantBridgeService);
    const options = {
      graph, target: { kind: 'node' as const, graphId: 'g', entityId: 'first', stepId: 'first', role: 'worker' },
      editorMode: 'full-editor' as const,
    };
    service.preview(options);
    service.select(options);
    await vi.advanceTimersByTimeAsync(500);
    expect(service.visible()).toBe(false);
    expect(service.ask('Nicht erlaubt')).toBe(false);
    expect(assemble).not.toHaveBeenCalled();
    expect(service.patchAllowed()).toBe(false);
  });

  it('keeps a pinned selection stable while guide SSE updates its visible explanation', async () => {
    const service = TestBed.inject(VpAssistantBridgeService);
    service.select({ graph, target: { kind: 'palette_item', graphId: 'g', entityId: 'selected', role: 'node-template' }, editorMode: 'full-editor' });
    await Promise.resolve();
    service.setPinned(true);
    service.preview({ graph, target: { kind: 'palette_item', graphId: 'g', entityId: 'hover', role: 'node-template' }, editorMode: 'full-editor' });
    await vi.advanceTimersByTimeAsync(500);
    expect(service.target()?.entityId).toBe('selected');
    guide.next([{ bubble: 'Hub-Hinweis' }]);
    expect(service.response()?.summary).toBe('Hub-Hinweis');
  });

  it('creates a typed context and conversation, submits idempotently and polls to completion', async () => {
    const service = TestBed.inject(VpAssistantBridgeService);
    const graphWithCatalog = {
      ...graph,
      metadata: {
        source_refs: [{ source_id: 'browser-supplied-unverified-reference', content: 'browser content must never be sent' }],
        source_catalog: { catalog_task_id: 'task-1', catalog_id: 'catalog-1', catalog_hash: 'hash-1' },
      },
    };
    service.select({ graph: graphWithCatalog, target: { kind: 'node', graphId: 'g', entityId: 'first', stepId: 'first', role: 'worker' }, editorMode: 'full-editor' });
    await Promise.resolve();
    expect(service.ask('Was macht dieser Node?')).toBe(true);
    await Promise.resolve(); await Promise.resolve();

    expect(api.createContext).toHaveBeenCalledWith(expect.objectContaining({
      graph_id: 'g', repository_revision: 'repo-1', codecompass_manifest_hash: 'manifest-1',
      source_allowlist_version: 'allow-1', source_scope: 'repository',
      catalog_task_id: 'task-1', catalog_id: 'catalog-1', catalog_hash: 'hash-1',
    }));
    expect(api.createContext.mock.calls[0][0]).not.toHaveProperty('source_refs');
    expect(api.createContext.mock.calls[0][0].draft_graph?.metadata).not.toHaveProperty('source_refs');
    expect(api.createConversation).toHaveBeenCalledWith('remote-first');
    expect(api.submitQuestion).toHaveBeenCalledWith('conversation-1', 'Was macht dieser Node?', expect.stringMatching(/^vp-client-/), expect.stringMatching(/^vp-idempotency-/));
    expect(service.requestStatus()).toBe('queued_retrieval');
    await vi.advanceTimersByTimeAsync(1_000);
    expect(api.getRequest).toHaveBeenCalledWith('request-1');
    expect(service.requestStatus()).toBe('completed');
    expect(service.response()?.summary).toBe('Hub-Antwort');
  });

  it('requires visible confirmation before switching an existing conversation context', async () => {
    const service = TestBed.inject(VpAssistantBridgeService);
    service.select({ graph, target: { kind: 'node', graphId: 'g', entityId: 'first', stepId: 'first', role: 'worker' }, editorMode: 'full-editor' });
    await Promise.resolve(); service.ask('Erste Frage'); await Promise.resolve(); await Promise.resolve();
    service.activeRequest.set(request('completed'));
    service.requestStatus.set('completed');

    service.select({ graph, target: { kind: 'node', graphId: 'g', entityId: 'changed', stepId: 'changed', role: 'worker' }, editorMode: 'full-editor' });
    await Promise.resolve(); expect(service.ask('Frage im neuen Kontext')).toBe(true); await Promise.resolve(); await Promise.resolve();
    expect(service.contextSwitchPending()).toBe(true);
    expect(api.switchConversationContext).not.toHaveBeenCalled();
    const submitsBeforeConfirmation = api.submitQuestion.mock.calls.length;

    service.confirmContextSwitch();
    expect(api.switchConversationContext).toHaveBeenCalledWith('conversation-1', 'remote-changed');
    expect(api.submitQuestion.mock.calls.length).toBe(submitsBeforeConfirmation + 1);
    expect(service.contextSwitchPending()).toBe(false);
  });

  it('cancels an active Hub request and exposes retry for terminal failures', async () => {
    const service = TestBed.inject(VpAssistantBridgeService);
    service.select({ graph, target: { kind: 'node', graphId: 'g', entityId: 'first', stepId: 'first', role: 'worker' }, editorMode: 'full-editor' });
    await Promise.resolve(); service.ask('Frage'); await Promise.resolve(); await Promise.resolve();
    expect(service.canCancel()).toBe(true);
    service.cancelRequest();
    expect(api.cancelRequest).toHaveBeenCalledWith('request-1');
    expect(service.requestStatus()).toBe('cancelled');
    expect(service.canRetry()).toBe(true);
    service.retryRequest();
    expect(api.retryRequest).toHaveBeenCalledWith('request-1', expect.stringMatching(/^vp-client-/), expect.stringMatching(/^vp-idempotency-/));
  });

  it('previews and applies a Hub-governed workflow patch only after an unchanged draft check', async () => {
    const service = TestBed.inject(VpAssistantBridgeService);
    service.select({ graph, target: { kind: 'node', graphId: 'g', entityId: 'first', stepId: 'first', role: 'worker' }, editorMode: 'full-editor' });
    await Promise.resolve();
    service.activeRequest.set(request('completed', {
      contract_version: 'ananta.visual_process.help_response.v1', context_id: 'remote-first', prompt_version: 'v1',
      summary: 'Patch', location: { target_kind: 'node', graph_id: 'g' }, explanation: '', options: [], warnings: [],
      next_actions: [], evidence: [], claims: [], workflow_patch: workflowPatch, extensions: {},
    }));
    service.requestStatus.set('completed');
    service.previewWorkflowPatch(graph);
    expect(api.previewPatch).toHaveBeenCalledWith('request-1', workflowPatch, graph);
    expect(service.patchStatus()).toBe('ready');
    const apply = vi.fn(() => true);
    service.acceptWorkflowPatch(() => graph, apply);
    expect(api.decidePatch).toHaveBeenCalledWith('request-1', 'patch-hash', 'accepted', true, graph);
    expect(apply).toHaveBeenCalledTimes(1);
    expect(service.patchStatus()).toBe('applied');
  });

  it('blocks patch acceptance after any local draft change', async () => {
    const service = TestBed.inject(VpAssistantBridgeService);
    service.select({ graph, target: { kind: 'node', graphId: 'g', entityId: 'first', stepId: 'first', role: 'worker' }, editorMode: 'full-editor' });
    await Promise.resolve();
    service.activeRequest.set(request('completed', {
      contract_version: 'ananta.visual_process.help_response.v1', context_id: 'remote-first', prompt_version: 'v1',
      summary: 'Patch', location: { target_kind: 'node', graph_id: 'g' }, explanation: '', options: [], warnings: [],
      next_actions: [], evidence: [], claims: [], workflow_patch: workflowPatch, extensions: {},
    }));
    service.requestStatus.set('completed'); service.previewWorkflowPatch(graph);
    const apply = vi.fn(() => true);
    service.acceptWorkflowPatch(() => ({ ...graph, name: 'Manuell geändert' }), apply);
    expect(service.patchStatus()).toBe('conflict');
    expect(service.patchError()).toBe('assistant_patch_draft_changed_after_preview');
    expect(apply).not.toHaveBeenCalled();
  });

  it('creates and auto-previews a new Hub request from the current draft after a patch conflict', async () => {
    const service = TestBed.inject(VpAssistantBridgeService);
    service.select({ graph, target: { kind: 'node', graphId: 'g', entityId: 'first', stepId: 'first', role: 'worker' }, editorMode: 'full-editor' });
    await Promise.resolve();
    const firstResponse = {
      contract_version: 'ananta.visual_process.help_response.v1' as const, context_id: 'remote-first', prompt_version: 'v1',
      summary: 'Patch', location: { target_kind: 'node' as const, graph_id: 'g' }, explanation: '', options: [], warnings: [],
      next_actions: [], evidence: [], claims: [], workflow_patch: workflowPatch, extensions: {},
    };
    service.activeRequest.set(request('completed', firstResponse));
    service.requestStatus.set('completed');
    service.previewWorkflowPatch(graph);
    const currentDraft = { ...graph, name: 'Manuell geändert' };
    service.acceptWorkflowPatch(() => currentDraft, vi.fn(() => true));
    expect(service.patchStatus()).toBe('conflict');

    const refreshedPatch = { ...workflowPatch, operations: [{
      ...workflowPatch.operations[0], operation_id: 'op-refreshed', temp_id: 'new-step-refreshed',
    }] };
    api.getRequest.mockReturnValueOnce(of({
      ...request('completed', { ...firstResponse, context_id: 'remote-refresh', workflow_patch: refreshedPatch }),
      request_id: 'request-refresh', context_id: 'remote-refresh',
    }));
    service.refreshWorkflowPatch(() => currentDraft);

    expect(api.refreshPatch).toHaveBeenCalledWith(
      'request-1',
      expect.objectContaining({ draft_graph: currentDraft, client_request_id: expect.stringMatching(/^vp-client-/) }),
      expect.stringMatching(/^vp-idempotency-/),
    );
    expect(service.patchStatus()).toBe('loading');
    expect(service.patchPreview()).not.toBeNull();
    await vi.advanceTimersByTimeAsync(1_000);

    expect(service.activeRequest()?.request_id).toBe('request-refresh');
    expect(api.previewPatch).toHaveBeenLastCalledWith('request-refresh', refreshedPatch, currentDraft);
    expect(service.patchProposal()?.operations[0].operation_id).toBe('op-refreshed');
    expect(service.patchStatus()).toBe('ready');
    expect(service.patchBaseGraph()?.name).toBe('Manuell geändert');
  });

  it('allows patches only for a current completed response and fails closed for every non-current outcome', async () => {
    const service = TestBed.inject(VpAssistantBridgeService);
    service.select({ graph, target: { kind: 'node', graphId: 'g', entityId: 'first', stepId: 'first', role: 'worker' }, editorMode: 'full-editor' });
    await Promise.resolve();
    const response = {
      contract_version: 'ananta.visual_process.help_response.v1' as const, context_id: 'remote-first', prompt_version: 'v1',
      summary: 'Patch', location: { target_kind: 'node' as const, graph_id: 'g' }, explanation: '', options: [], warnings: [],
      next_actions: [], evidence: [], claims: [], workflow_patch: workflowPatch, extensions: {},
    };
    service.activeRequest.set(request('completed', response));
    service.requestStatus.set('completed');
    service.errorCode.set(null);
    expect(service.patchAllowed()).toBe(true);

    const cases = [
      ['failed', 'assistant_context_stale', 'stale'],
      ['failed', 'assistant_graph_revision_conflict', 'conflict'],
      ['rejected', 'assistant_policy_rejected', 'rejected'],
      ['completed', 'assistant_no_results', 'no_results'],
      ['timeout', 'assistant_request_timeout', 'timeout'],
      ['cancelled', 'assistant_request_cancelled', 'cancelled'],
    ] as const;
    for (const [status, errorCode, outcome] of cases) {
      service.activeRequest.set({ ...request(status, response), error_code: errorCode });
      service.requestStatus.set(status);
      service.errorCode.set(errorCode);
      expect(service.outcome().state, errorCode).toBe(outcome);
      expect(service.patchAllowed(), errorCode).toBe(false);
      api.previewPatch.mockClear();
      service.previewWorkflowPatch(graph);
      expect(api.previewPatch, errorCode).not.toHaveBeenCalled();
    }
  });

  it('resumes a persisted Hub conversation after an editor reload', async () => {
    sessionStorage.setItem('ananta.visual-process.assistant.conversation.v1:g', 'conversation-restored');
    api.getConversation.mockReturnValueOnce(of({
      conversation_id: 'conversation-restored', graph_id: 'g', status: 'active', active_context_id: 'remote-first',
      created_at: 1, updated_at: 2, requests: [],
    }));
    const service = TestBed.inject(VpAssistantBridgeService);
    service.select({ graph, target: { kind: 'node', graphId: 'g', entityId: 'first', stepId: 'first', role: 'worker' }, editorMode: 'full-editor' });
    await Promise.resolve(); expect(service.ask('Nach Reload')).toBe(true); await Promise.resolve(); await Promise.resolve();
    expect(api.getConversation).toHaveBeenCalledWith('conversation-restored');
    expect(api.createConversation).not.toHaveBeenCalled();
    expect(api.submitQuestion).toHaveBeenCalledWith('conversation-restored', 'Nach Reload', expect.any(String), expect.any(String));
  });

  it('surfaces a tenant-forbidden context as a distinct 403 state', async () => {
    api.createContext.mockReturnValueOnce(throwError(() => new HttpErrorResponse({
      status: 403, error: { error_code: 'assistant_source_tenant_forbidden' },
    })));
    const service = TestBed.inject(VpAssistantBridgeService);
    service.select({ graph, target: { kind: 'node', graphId: 'g', entityId: 'first', stepId: 'first', role: 'worker' }, editorMode: 'full-editor' });
    await Promise.resolve(); service.ask('Tenant?'); await Promise.resolve(); await Promise.resolve();
    expect(service.requestStatus()).toBe('error');
    expect(service.errorCode()).toBe('assistant_source_tenant_forbidden');
  });
});
