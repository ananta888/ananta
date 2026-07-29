import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { UnslothMutationCommand } from '../model-training.models';
import { UnslothHubMutationService } from './unsloth-hub-mutation.service';

describe('UnslothHubMutationService', () => {
  let service: UnslothHubMutationService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(UnslothHubMutationService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('posts an allowlisted command only to the Hub mutation route', () => {
    const command: UnslothMutationCommand = {
      operation: 'runtime_handoff',
      resource_id: 'adapter_01JABC',
      reason: 'Prepare runtime handoff',
      dry_run: true,
      confirmed: false,
      promoted_artifact_id: 'lora-export-01JABC',
      promoted_artifact_sha256: 'a'.repeat(64),
      provider_descriptor: {
        provider_id: 'local-provider',
        provider_type: 'local-openai-compatible',
        model_id: 'local/model',
        provider_revision: 'revision-1',
        capabilities: {
          openai_chat: true,
          openai_responses: false,
          anthropic_messages: false,
          streaming: true,
          tools: true,
          structured_output: true,
        },
        limits: {
          timeout_seconds: 60,
          context_tokens: 8192,
          max_output_tokens: 2048,
          stream_idle_timeout_seconds: 30,
        },
      },
      endpoint_descriptor: {
        endpoint_id: 'endpoint-a',
        display_name: 'Local adapter',
        routing_key: 'adapter-a',
      },
      expected_endpoint_revision: 0,
      source_ids: ['SRC_supplied-model'],
      run_ids: ['RUN_supplied-evaluation'],
    };

    service
      .submit('https://hub.example.test:8443', command, 'idem-01JABCDEF')
      .subscribe();

    const request = http.expectOne(
      'https://hub.example.test:8443/api/ml-intern-training/unsloth/mutations/runtime_handoff',
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual(command);
    expect(request.request.headers.get('Idempotency-Key')).toBe('idem-01JABCDEF');
    request.flush({
      data: {
        accepted: true,
        operation: 'runtime_handoff',
        dry_run: true,
      },
    });
  });

  it('posts a revision-fenced cleanup only to the Hub mutation route', () => {
    const command: UnslothMutationCommand = {
      operation: 'cleanup',
      resource_id: 'tenant-storage',
      reason: 'Remove expired storage artifacts',
      dry_run: true,
      confirmed: false,
      artifact_ids: ['artifact-storage-1'],
      expected_catalog_revision: 7,
    };

    service.submit('https://hub.example.test', command, 'idem-cleanup-01').subscribe();

    const request = http.expectOne(
      'https://hub.example.test/api/ml-intern-training/unsloth/mutations/cleanup',
    );
    expect(request.request.body).toEqual(command);
    request.flush({
      data: {
        accepted: true,
        operation: 'cleanup',
        dry_run: true,
        confirmation_id: 'confirmation-cleanup-01',
      },
    });
  });
});
