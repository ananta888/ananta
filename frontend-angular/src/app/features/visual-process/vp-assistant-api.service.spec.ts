import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { VpAssistantApiService, VpAssistantContextCreateRequest } from './vp-assistant-api.service';

describe('VpAssistantApiService', () => {
  let api: VpAssistantApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [
      VpAssistantApiService,
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: AgentDirectoryService, useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'http://hub' }] } },
    ] });
    api = TestBed.inject(VpAssistantApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('creates a typed immutable context through the versioned Hub API', () => {
    const body: VpAssistantContextCreateRequest = {
      graph_id: 'graph-1', location: { target_kind: 'node', graph_id: 'graph-1', entity_id: 'step-1' },
      editor_mode: 'editor', repository_revision: 'repo-1', codecompass_manifest_hash: 'manifest-1',
      source_allowlist_version: 'allowlist-1', source_scope: 'repository',
      catalog_task_id: 'task-1', catalog_id: 'catalog-1', catalog_hash: 'hash-1',
    };
    api.createContext(body).subscribe();
    const request = http.expectOne('http://hub/api/visual-process/assistant/v1/contexts');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual(body);
    expect(request.request.body).not.toHaveProperty('source_refs');
    request.flush({ context_id: 'ctx-1' });
  });

  it('uses an Idempotency-Key and a separate client_request_id for questions and retries', () => {
    api.submitQuestion('conversation/1', 'Was macht der Node?', 'client-1', 'idem-1').subscribe();
    const question = http.expectOne('http://hub/api/visual-process/assistant/v1/conversations/conversation%2F1/questions');
    expect(question.request.method).toBe('POST');
    expect(question.request.headers.get('Idempotency-Key')).toBe('idem-1');
    expect(question.request.body).toEqual({ question: 'Was macht der Node?', client_request_id: 'client-1' });
    question.flush({ request_id: 'request-1', status: 'queued_retrieval' });

    api.retryRequest('request/1', 'client-2', 'idem-2').subscribe();
    const retry = http.expectOne('http://hub/api/visual-process/assistant/v1/requests/request%2F1/retry');
    expect(retry.request.headers.get('Idempotency-Key')).toBe('idem-2');
    expect(retry.request.body).toEqual({ client_request_id: 'client-2' });
    retry.flush({ request_id: 'request-2', status: 'queued_retrieval' });
  });

  it('exposes polling, cancellation, confirmed context switch and patch governance endpoints', () => {
    api.getRequest('request-1').subscribe();
    http.expectOne('http://hub/api/visual-process/assistant/v1/requests/request-1').flush({});
    api.cancelRequest('request-1').subscribe();
    const cancel = http.expectOne('http://hub/api/visual-process/assistant/v1/requests/request-1/cancel');
    expect(cancel.request.method).toBe('POST'); cancel.flush({});
    api.switchConversationContext('conversation-1', 'ctx-sha256:one').subscribe();
    const switched = http.expectOne('http://hub/api/visual-process/assistant/v1/conversations/conversation-1/context-switch');
    expect(switched.request.body).toEqual({ context_id: 'ctx-sha256:one', confirmed: true }); switched.flush({});
    api.previewPatch('request-1').subscribe();
    const preview = http.expectOne('http://hub/api/visual-process/assistant/v1/requests/request-1/patch-preview');
    expect(preview.request.body).toEqual({}); preview.flush({});
    const draft = { id: 'graph-1', name: 'Draft', description: '', version: '1', tags: [], steps: [], edges: [] };
    api.previewPatch('request-2', undefined, draft).subscribe();
    const draftPreview = http.expectOne('http://hub/api/visual-process/assistant/v1/requests/request-2/patch-preview');
    expect(draftPreview.request.body).toEqual({ draft_graph: draft }); draftPreview.flush({});
    api.refreshPatch('request-1', { draft_graph: draft, client_request_id: 'refresh-client' }, 'refresh-idem').subscribe();
    const refresh = http.expectOne('http://hub/api/visual-process/assistant/v1/requests/request-1/patch-refresh');
    expect(refresh.request.method).toBe('POST');
    expect(refresh.request.headers.get('Idempotency-Key')).toBe('refresh-idem');
    expect(refresh.request.body).toEqual({ draft_graph: draft, client_request_id: 'refresh-client' });
    refresh.flush({ request_id: 'request-2', status: 'queued_retrieval' });
    api.decidePatch('request-1', 'hash-1', 'accepted', true).subscribe();
    const decision = http.expectOne('http://hub/api/visual-process/assistant/v1/requests/request-1/patch-decisions');
    expect(decision.request.body).toEqual({ patch_hash: 'hash-1', decision: 'accepted', confirmed: true });
    decision.flush({});
    api.decidePatch('request-2', 'hash-2', 'accepted', true, draft).subscribe();
    const draftDecision = http.expectOne('http://hub/api/visual-process/assistant/v1/requests/request-2/patch-decisions');
    expect(draftDecision.request.body).toEqual({ patch_hash: 'hash-2', decision: 'accepted', confirmed: true, draft_graph: draft });
    draftDecision.flush({});
  });
});
