import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { BusinessControllingApiService } from './business-controlling-api.service';

describe('BusinessControllingApiService', () => {
  let api: BusinessControllingApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(BusinessControllingApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('keeps tenant authority out of the browser and loads versioned findings', () => {
    api.findings({ project_id: 'project-a' }).subscribe(findings => {
      expect(findings[0].finding_id).toBe('finding-a');
    });

    const request = http.expectOne(req => req.url === '/api/v1/controlling/findings');
    expect(request.request.params.get('project_id')).toBe('project-a');
    expect(request.request.params.has('tenant_id')).toBe(false);
    request.flush({ findings: [{ finding_id: 'finding-a' }] });
  });

  it('sends OCC disposition and explicit server-bounded run options', () => {
    const scope = { project_id: 'project-a' };
    const finding = {
      finding_id: 'finding-a',
      kind: 'statistical_anomaly' as const,
      severity: 'medium' as const,
      dataset_version: 'dataset-a',
      rule_version: 'v1',
      confidence: 0.8,
      evidence_digest: 'a'.repeat(64),
      disposition: 'open' as const,
      revision: 3,
    };

    api.startRun(scope, 'b'.repeat(64), false, true).subscribe();
    const run = http.expectOne('/api/v1/controlling/runs');
    expect(run.request.body.statistics_enabled).toBe(false);
    expect(run.request.body.explanations_enabled).toBe(true);
    run.flush({ run: { run_id: 'run-a', status: 'completed', finding_count: 1 } });

    api.setDisposition(scope, finding, 'confirmed').subscribe();
    const disposition = http.expectOne('/api/v1/controlling/findings/finding-a/disposition');
    expect(disposition.request.body.expected_revision).toBe(3);
    disposition.flush({ finding: { ...finding, disposition: 'confirmed', revision: 4 } });
  });
});
