import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { SourceControlIndexAccessApiClient } from './source-control-index-access-api.client';

describe('SourceControlIndexAccessApiClient', () => {
  let client: SourceControlIndexAccessApiClient;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        SourceControlIndexAccessApiClient,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    client = TestBed.inject(SourceControlIndexAccessApiClient);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('reads preparation with authoritative project scope and matching strong ETag', () => {
    let received: any;
    client.prepare('connection-example', 'project-alpha').subscribe(value => { received = value; });

    const request = http.expectOne(candidate =>
      candidate.url === '/api/source-control/v1/connections/connection-example/actions/prepare-index-access'
      && candidate.params.get('project_id') === 'project-alpha',
    );
    expect(request.request.method).toBe('GET');
    request.flush(envelope(preparation()), { headers: { ETag: `"${'a'.repeat(64)}"` } });
    expect(received.options[0].preset_id).toBe('preset-redacted-index');
  });

  it('posts only the selected server IDs, duration and explicit confirmation', () => {
    const prepared = preparation();
    client.grant(
      prepared,
      'project-alpha',
      {
        destinationId: 'destination-example',
        optionId: 'redacted-local-once',
        durationSeconds: 900,
        confirmed: true,
      },
      'index-access-example',
    ).subscribe();

    const request = http.expectOne(candidate =>
      candidate.url === '/api/source-control/v1/connections/connection-example/actions/prepare-index-access'
      && candidate.params.get('project_id') === 'project-alpha',
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({
      source_revision_id: 'source-revision-example',
      destination_id: 'destination-example',
      option_id: 'redacted-local-once',
      duration_seconds: 900,
      confirmed: true,
    });
    expect(request.request.headers.get('If-Match')).toBe(`"${'a'.repeat(64)}"`);
    expect(request.request.headers.get('Idempotency-Key')).toBe('index-access-example');
    request.flush(envelope(result()), { headers: { ETag: `"${'c'.repeat(64)}"` } });
  });

  it('rejects browser-invented destinations and missing confirmation before HTTP', () => {
    const prepared = preparation();
    expect(() => client.grant(
      prepared,
      'project-alpha',
      {
        destinationId: 'browser-invented',
        optionId: 'redacted-local-once',
        durationSeconds: 900,
        confirmed: true,
      },
      'index-access-rejected',
    )).toThrow('index_access_selection_invalid');
    http.expectNone(() => true);
  });
});

function envelope(data: unknown): unknown {
  return { schema: 'ananta.source-control.api-response.v1', data };
}

function preparation(): any {
  return {
    connection_id: 'connection-example',
    source_revision: {
      source_revision_id: 'source-revision-example',
      revision_digest: '1'.repeat(64),
      admission_state: 'admitted',
      captured_at: '2026-08-01T12:00:00Z',
    },
    destinations: [{
      destination_id: 'destination-example', worker_id: 'worker-example', runtime_kind: 'codecompass',
      provider_location: 'local_container', data_residency: 'local',
    }],
    options: [{
      option_id: 'redacted-local-once', preset_id: 'preset-redacted-index', label: 'Lokal redigiert',
      effect: { provider_location: 'local', transformation: 'redacted', one_time: true },
      duration_seconds: { minimum: 60, maximum: 900, default: 900 },
    }],
    readiness: { ready: true, reason_codes: [] },
    etag: 'a'.repeat(64),
  };
}

function result(): any {
  return {
    access_ready: true,
    connection_id: 'connection-example',
    source_revision_id: 'source-revision-example',
    destination_id: 'destination-example',
    option_id: 'redacted-local-once',
    effect: { provider_location: 'local', transformation: 'redacted', one_time: true },
    policy: { policy_id: 'policy-example', version: 1, state: 'active', etag: 'b'.repeat(64) },
    grant: { grant_id: 'grant-example', state: 'active', etag: 'c'.repeat(64), expires_at: '2026-08-01T12:15:00Z' },
    next_actions: ['start_index_run'],
  };
}
