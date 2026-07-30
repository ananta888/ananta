import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  CodeCompassLifecycleClient,
  CodeCompassReadClient,
} from './codecompass-api.client';
import { HubApiCoreService } from './hub-api-core.service';

describe('CodeCompass API ports', () => {
  const core = { get: vi.fn() };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        CodeCompassReadClient,
        CodeCompassLifecycleClient,
        { provide: HubApiCoreService, useValue: core },
      ],
    });
  });

  it('binds every architecture query to knowledge_index_id and uses GET', async () => {
    core.get.mockReturnValue(of({
      schema: 'architecture_query.v1',
      metadata: { knowledge_index_id: 'index-1' },
    }));
    const client = TestBed.inject(CodeCompassReadClient);

    await firstValueFrom(client.query('http://hub.test', {
      schema: 'codecompass_query.v1',
      knowledge_index_id: 'index-1',
      query_type: 'symbol_detail',
      seed: 'symbol-1',
    }));

    expect(core.get.mock.calls[0][0]).toContain('/api/codecompass/query?');
    expect(core.get.mock.calls[0][0]).toContain('knowledge_index_id=index-1');
    expect(core.get.mock.calls[0][0]).toContain('type=symbol_detail');
  });

  it('uses the implemented knowledge index and graph routes', async () => {
    core.get
      .mockReturnValueOnce(of({ items: [] }))
      .mockReturnValueOnce(of({ metadata: { knowledge_index_id: 'index-1' }, nodes: [], edges: [] }));
    const client = TestBed.inject(CodeCompassReadClient);

    await firstValueFrom(client.listIndexes('http://hub.test'));
    await firstValueFrom(client.getGraph('http://hub.test', 'index-1'));

    expect(core.get.mock.calls[0][0]).toContain('/knowledge/indices?');
    expect(core.get.mock.calls[1][0]).toContain('/api/codecompass/graph?knowledge_index_id=index-1');
  });

  it('does not advertise lifecycle routes that do not exist', () => {
    const lifecycle = TestBed.inject(CodeCompassLifecycleClient);
    expect(lifecycle.capabilities()).toEqual(expect.objectContaining({
      reindex: false,
      activate: false,
      rollback: false,
      cleanup: false,
    }));
  });
});
