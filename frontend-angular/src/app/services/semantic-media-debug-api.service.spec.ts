import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import { SemanticMediaDebugApiService, parseSemanticDebugEvent } from './semantic-media-debug-api.service';

const event = (patch: Record<string, unknown> = {}) => ({
  event_id: 'audit-event-a', tenant_digest: 'a'.repeat(64), scope_digest: 'b'.repeat(64),
  event_type: 'semantic_contract', transition: 'activated', reason_code: 'hub_confirmed', epoch: 3,
  contract_ref: 'c'.repeat(64), lease_ref: null, job_ref: null,
  created_at_ms: 1_000, expires_at_ms: 2_000, ...patch,
});

describe('SemanticMediaDebugApiService', () => {
  const request = vi.fn();
  let service: SemanticMediaDebugApiService;

  beforeEach(() => {
    request.mockReset();
    TestBed.configureTestingModule({ providers: [
      SemanticMediaDebugApiService,
      { provide: HubApiCoreService, useValue: { request } },
    ] });
    service = TestBed.inject(SemanticMediaDebugApiService);
  });

  it('loads the role-scoped read-only page by logical Hub scope', async () => {
    request.mockReturnValue(of({ ok: true, data: { items: [event()], next_cursor: 'audit-event-a', read_only: true } }));
    const page = await new Promise<any>((resolve, reject) => service.page(
      'http://hub.test/', 'semantic-contract:session-a', null, 25,
    ).subscribe({ next: resolve, error: reject }));
    expect(page.items).toHaveLength(1);
    expect(page.items[0]).not.toHaveProperty('tenant_digest');
    expect(request.mock.calls[0][1]).toContain('scope=semantic-contract%3Asession-a&limit=25');
  });

  it('rejects extra fields, content values and invalid temporal bounds', () => {
    expect(() => parseSemanticDebugEvent(event({ transcript: 'secret' })))
      .toThrow('semantic_debug_event_shape_invalid');
    expect(() => parseSemanticDebugEvent(event({ expires_at_ms: 999 })))
      .toThrow('semantic_debug_integer_invalid');
  });
});
