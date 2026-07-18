import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting, HttpTestingController } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { VisualProcessApiService, VpGraph, sortValidationIssues } from './visual-process-api.service';

function graph(): VpGraph {
  return { id: 'graph-1', name: 'Graph', description: '', version: '1', tags: [], steps: [], edges: [] };
}

describe('VisualProcessApiService definition contracts', () => {
  let api: VisualProcessApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [
      VisualProcessApiService,
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub' }] } },
    ] });
    api = TestBed.inject(VisualProcessApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('loads the registry-driven node definitions from the Hub', () => {
    api.listNodeDefinitions().subscribe();
    const request = http.expectOne('http://hub/api/visual-process/node-definitions');
    expect(request.request.method).toBe('GET');
    request.flush({ schema: 'ananta.visual_process.node_definition_registry.v1', definitions: [] });
  });

  it('keeps the compatibility create path for a new graph', () => {
    const draft = graph();
    api.saveGraph(draft).subscribe();
    const request = http.expectOne('http://hub/api/visual-process/graphs');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual(draft);
    request.flush({ id: draft.id, version: '1', definition_revision: 1, base_graph_hash: 'a'.repeat(64), saved: true });
  });

  it('uses revision and If-Match for an existing graph', () => {
    const draft = { ...graph(), definition_revision: 3, base_graph_hash: 'b'.repeat(64) };
    api.saveGraph(draft).subscribe();
    const request = http.expectOne('http://hub/api/visual-process/v2/graphs/graph-1');
    expect(request.request.method).toBe('PUT');
    expect(request.request.headers.get('If-Match')).toBe(`"${draft.base_graph_hash}"`);
    expect(request.request.body).toEqual({ graph: draft, expected_revision: 3, base_graph_hash: draft.base_graph_hash });
    request.flush({ id: draft.id, version: '1', definition_revision: 4, base_graph_hash: 'c'.repeat(64), saved: true });
  });

  it('sorts validation issues like the Hub by severity, code and canonical path', () => {
    const issues = sortValidationIssues([
      { severity: 'warning', code: 'z-code', path: '/z', message: 'Z' },
      { severity: 'error', code: 'b-code', path: '/b', message: 'B' },
      { severity: 'error', code: 'a-code', path: '/a', message: 'A' },
    ]);
    expect(issues.map(issue => [issue.severity, issue.code, issue.path])).toEqual([
      ['error', 'a-code', '/a'], ['error', 'b-code', '/b'], ['warning', 'z-code', '/z'],
    ]);
  });
});
