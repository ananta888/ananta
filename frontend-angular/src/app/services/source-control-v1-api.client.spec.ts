import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { SourceControlV1ApiClient } from './source-control-v1-api.client';


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

  it('sends only a server workspace id for validation', () => {
    client.validateConnection({
      connector_type: 'registered_workspace',
      workspace_id: 'workspace-example',
      display_name: 'Workspace',
      sensitivity: 'internal',
    }).subscribe();

    const request = http.expectOne(
      '/api/source-control/v1/connections/validate',
    );
    expect(request.request.body).toEqual({
      connector_type: 'registered_workspace',
      workspace_id: 'workspace-example',
      display_name: 'Workspace',
      sensitivity: 'internal',
      dry_run: true,
    });
    expect(request.request.body.connection_identity_digest).toBeUndefined();
    expect(request.request.body.path).toBeUndefined();
    expect(request.request.body.url).toBeUndefined();
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
});
