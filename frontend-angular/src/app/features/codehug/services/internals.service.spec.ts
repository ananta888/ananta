import { HttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { SourceControlV1ApiClient } from '../../../services/source-control-v1-api.client';
import { InternalsService } from './internals.service';


describe('InternalsService CodeCompass graph window', () => {
  const graph = {
    schema: 'domain_graph_artifact.v1',
    source_kind: 'codecompass_graph',
    source_ref: 'index-example',
    nodes: [],
    edges: [],
    metadata: { node_count: 0, edge_count: 0 },
    warnings: [],
    text_alternative: 'Empty graph.',
    artifact_status: { state: 'available' },
  };
  const sourceControlApi = {
    loadGraph: vi.fn(() => of(graph)),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        InternalsService,
        { provide: HttpClient, useValue: {} },
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
        { provide: SourceControlV1ApiClient, useValue: sourceControlApi },
      ],
    });
  });

  it('requests the largest bounded topology-preserving project graph window', async () => {
    const service = TestBed.inject(InternalsService);

    const result = await firstValueFrom(
      service.getCodeCompassGraph('connection-example'),
    );

    expect(sourceControlApi.loadGraph).toHaveBeenCalledWith(
      'connection-example',
      { limit: 500, view: 'topology', maxEdges: 2_000 },
    );
    expect(result).toBe(graph);
  });
});
