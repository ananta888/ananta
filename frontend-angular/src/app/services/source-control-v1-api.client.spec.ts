import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import {
  SourceControlGraphQuery,
  SourceControlV1ApiClient,
} from './source-control-v1-api.client';


describe('SourceControlV1ApiClient security DTOs', () => {
  let client: SourceControlV1ApiClient;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        SourceControlV1ApiClient,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    client = TestBed.inject(SourceControlV1ApiClient);
    http = TestBed.inject(HttpTestingController);
  });

  it('encodes the bounded topology graph window query', () => {
    client.loadGraph('connection-example', {
      limit: 500,
      view: 'topology',
      maxEdges: 2_000,
    }).subscribe();

    const request = http.expectOne(
      '/api/source-control/v1/connections/connection-example/graph?limit=500&view=topology&max_edges=2000',
    );
    expect(request.request.method).toBe('GET');
  });

  it('encodes a domain scope and its subdomain policy', () => {
    client.loadGraph('connection-example', {
      view: 'topology',
      domainScope: 'domain:frontend',
      includeSubdomains: true,
    }).subscribe();

    const request = http.expectOne((candidate) =>
      candidate.url === '/api/source-control/v1/connections/connection-example/graph'
      && candidate.params.get('view') === 'topology'
      && candidate.params.get('domain_scope') === 'domain:frontend'
      && candidate.params.get('include_subdomains') === 'true',
    );
    expect(request.request.method).toBe('GET');
  });

  it('serializes an explicit false subdomain policy', () => {
    client.loadGraph('connection-example', {
      view: 'staged',
      includeSubdomains: false,
    }).subscribe();

    const request = http.expectOne(
      '/api/source-control/v1/connections/connection-example/graph?view=staged&include_subdomains=false',
    );
    expect(request.request.method).toBe('GET');
  });

  it('encodes a lossless staged edge page', () => {
    client.loadGraph('connection-example', {
      view: 'staged',
      stage: 'edges',
      maxEdges: 500,
    }).subscribe();

    const request = http.expectOne(
      '/api/source-control/v1/connections/connection-example/graph?view=staged&max_edges=500&stage=edges',
    );
    expect(request.request.method).toBe('GET');
  });

  it('rejects an invalid graph domain scope before issuing a request', () => {
    expect(() => client.loadGraph('connection-example', {
      view: 'topology',
      domainScope: 'frontend domain',
    })).toThrow('domain_scope_invalid');

    http.expectNone(
      '/api/source-control/v1/connections/connection-example/graph',
    );
  });

  it('rejects a non-boolean subdomain policy before issuing a request', () => {
    expect(() => client.loadGraph('connection-example', {
      view: 'topology',
      includeSubdomains: 'true' as unknown as boolean,
    })).toThrow('include_subdomains_invalid');

    http.expectNone(
      '/api/source-control/v1/connections/connection-example/graph',
    );
  });

  it('rejects domain parameters when the graph view does not support a scope', () => {
    expect(() => client.loadGraph('connection-example', {
      view: 'inventory',
      domainScope: 'domain:frontend',
    } as unknown as SourceControlGraphQuery))
      .toThrow('graph_domain_view_invalid');

    http.expectNone(
      '/api/source-control/v1/connections/connection-example/graph',
    );
  });

  it('rejects an oversized graph edge window before issuing a request', () => {
    expect(() => client.loadGraph('connection-example', {
      view: 'topology',
      maxEdges: 2_001,
    })).toThrow('max_edges_invalid');

    http.expectNone(
      '/api/source-control/v1/connections/connection-example/graph',
    );
  });

  it('sends only a server workspace id and safe relative path for validation', () => {
    client.validateConnection({
      connector_type: 'registered_workspace',
      workspace_id: 'workspace-example',
      relative_path: 'src/app',
      display_name: 'Workspace',
      sensitivity: 'internal',
    }).subscribe();

    const request = http.expectOne(
      '/api/source-control/v1/connections/validate',
    );
    expect(request.request.body).toEqual({
      connector_type: 'registered_workspace',
      workspace_id: 'workspace-example',
      relative_path: 'src/app',
      display_name: 'Workspace',
      sensitivity: 'internal',
      dry_run: true,
    });
    expect(request.request.body.connection_identity_digest).toBeUndefined();
    expect(request.request.body.path).toBeUndefined();
    expect(request.request.body.url).toBeUndefined();
  });

  it('rejects unsafe workspace relative paths before issuing a request', () => {
    expect(() => client.validateConnection({
      connector_type: 'registered_workspace',
      workspace_id: 'workspace-example',
      relative_path: '../secret',
      display_name: 'Workspace',
      sensitivity: 'internal',
    })).toThrowError('relative_path_invalid');

    http.expectNone('/api/source-control/v1/connections/validate');
  });

  it('sends only a registered remote id for creation', () => {
    client.createConnection({
      connector_type: 'github',
      remote_id: 'remote-example',
      display_name: 'Repository',
      sensitivity: 'internal',
    }, 'remote-create-example').subscribe();

    const request = http.expectOne('/api/source-control/v1/connections');
    expect(request.request.body.remote_id).toBe('remote-example');
    expect(request.request.body.url).toBeUndefined();
    expect(request.request.body.connection_identity_digest).toBeUndefined();
  });

  it('dispatches CodeHug without a browser write flag', () => {
    client.dispatchCodeHugMutation(
      'intent-example',
      'codehug-test-example',
    ).subscribe();

    const request = http.expectOne(
      '/api/source-control/v1/codehug/mutations',
    );
    expect(request.request.body).toEqual({
      mutation_intent_id: 'intent-example',
      dry_run: false,
    });
    expect(request.request.body.write_armed).toBeUndefined();
  });

  it('activates an index with the authoritative active-pointer CAS', () => {
    client.activateIndex('index-example', {
      etag: 'active:0',
      idempotencyKey: 'index-activate-example',
    }).subscribe();

    const request = http.expectOne(
      '/api/source-control/v1/indices/index-example/activate',
    );
    expect(request.request.body).toEqual({ dry_run: false });
    expect(request.request.headers.get('If-Match')).toBe('"active:0"');
    expect(request.request.headers.get('Idempotency-Key')).toBe(
      'index-activate-example',
    );
  });

  it('rejects a run-resource ETag for active-pointer mutations', () => {
    expect(() => client.activateIndex('index-example', {
      etag: 'index:2',
      idempotencyKey: 'index-activate-wrong-cas',
    })).toThrow('if_match_invalid');

    http.expectNone('/api/source-control/v1/indices/index-example/activate');
  });
});
