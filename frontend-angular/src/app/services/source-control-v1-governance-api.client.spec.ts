import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { SourceControlV1GovernanceApiClient } from './source-control-v1-governance-api.client';

describe('SourceControlV1GovernanceApiClient', () => {
  let api: SourceControlV1GovernanceApiClient;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
      providers: [SourceControlV1GovernanceApiClient],
    });
    api = TestBed.inject(SourceControlV1GovernanceApiClient);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('sends the exact dry-run content admission contract to the v1 validate endpoint', () => {
    const body = {
      project_id: 'project-alpha',
      source_type: 'direct_text' as const,
      display_name: 'Architecture',
      sensitivity: 'internal',
      content: '# Hub',
      media_type: 'text/markdown' as const,
      dry_run: true,
    };

    api.validateContentAdmission(body).subscribe({ error: () => undefined });

    const request = http.expectOne('/api/source-control/v1/content-admissions/validate');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual(body);
    expect(request.request.headers.has('Authorization')).toBeFalse();
    request.flush({ error: { code: 'test-stop' } }, { status: 400, statusText: 'Bad Request' });
  });

  it('persists admitted notebook content only through the production admission endpoint', () => {
    const body = {
      project_id: 'project-alpha',
      source_type: 'notebook' as const,
      display_name: 'Runbook',
      sensitivity: 'confidential',
      notebook: {
        cells: [
          {
            cell_type: 'code' as const,
            source: 'print("ok")',
            outputs: [{ output_type: 'stream' as const, text: 'ok' }],
          },
        ],
      },
      dry_run: false,
    };

    api.createContentAdmission(body).subscribe({ error: () => undefined });

    const request = http.expectOne('/api/source-control/v1/content-admissions');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual(body);
    request.flush({ error: { code: 'test-stop' } }, { status: 400, statusText: 'Bad Request' });
  });

  it('uses project-scoped v1 catalogs without client-owned authentication tokens', () => {
    api.listWorkspaces('project-alpha').subscribe({ error: () => undefined });

    const request = http.expectOne(
      (candidate) =>
        candidate.url === '/api/source-control/v1/workspaces' &&
        candidate.params.get('project_id') === 'project-alpha',
    );
    expect(request.request.method).toBe('GET');
    expect(request.request.headers.has('Authorization')).toBeFalse();
    request.flush({ error: { code: 'test-stop' } }, { status: 400, statusText: 'Bad Request' });
  });

  it('requires policy CAS and idempotency headers for grant creation', () => {
    const body = {
      source_revision_id: `srev_${'a'.repeat(64)}`,
      destination_id: 'hub-destination-primary',
      policy_id: 'policy-primary',
      preset_id: 'preset-read',
      duration_seconds: 900,
    };

    api
      .createGrant('project-alpha', body, '"policy-etag"', 'grant-create-001')
      .subscribe({ error: () => undefined });

    const request = http.expectOne(
      (candidate) =>
        candidate.url === '/api/source-control/v1/grants' &&
        candidate.params.get('project_id') === 'project-alpha',
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual(body);
    expect(request.request.headers.get('If-Match')).toBe('"policy-etag"');
    expect(request.request.headers.get('Idempotency-Key')).toBe('grant-create-001');
    request.flush({ error: { code: 'test-stop' } }, { status: 400, statusText: 'Bad Request' });
  });
});
