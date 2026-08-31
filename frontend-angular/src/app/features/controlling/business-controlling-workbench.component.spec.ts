import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';

import {
  BusinessControllingApiService,
  BusinessControllingFinding,
} from './business-controlling-api.service';
import { BusinessControllingWorkbenchComponent } from './business-controlling-workbench.component';

const finding: BusinessControllingFinding = {
  finding_id: 'finding-a',
  kind: 'deterministic_violation',
  severity: 'high',
  dataset_version: 'dataset-version-a',
  rule_version: 'v1',
  confidence: null,
  evidence_digest: 'a'.repeat(64),
  disposition: 'open',
  revision: 0,
};

describe('BusinessControllingWorkbenchComponent', () => {
  const api = {
    status: vi.fn().mockReturnValue(of({
      schema: 'ananta.business-controlling-status.v1',
      enabled: true,
      read_only: true,
      statistics_enabled: false,
      explanations_enabled: true,
    })),
    profileImport: vi.fn().mockReturnValue(of({
      profile_digest: 'b'.repeat(64),
      source_revision_id: 'source-a',
      row_count: 2,
      columns: [{ header: 'amount', inferred_type: 'decimal' }],
    })),
    confirmMapping: vi.fn().mockReturnValue(of({
      profile_digest: 'b'.repeat(64),
      confirmation_digest: 'c'.repeat(64),
      column_mapping: { amount: 'amount' },
    })),
    startRun: vi.fn().mockReturnValue(of({ run_id: 'run-a', status: 'completed', finding_count: 1 })),
    findings: vi.fn().mockReturnValue(of([finding])),
    setDisposition: vi.fn().mockReturnValue(of({ ...finding, disposition: 'confirmed', revision: 1 })),
    export: vi.fn().mockReturnValue(of({ report_digest: 'd'.repeat(64), content_redacted: true })),
  };

  beforeEach(async () => {
    Object.values(api).forEach(spy => spy.mockClear());
    await TestBed.configureTestingModule({
      imports: [BusinessControllingWorkbenchComponent],
      providers: [{ provide: BusinessControllingApiService, useValue: api }],
    }).compileComponents();
  });

  it('loads real server state and distinguishes finding categories', () => {
    const fixture = TestBed.createComponent(BusinessControllingWorkbenchComponent);
    fixture.componentRef.setInput('projectId', 'project-a');
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent;
    expect(api.status).toHaveBeenCalled();
    expect(api.findings).toHaveBeenCalled();
    expect(text).toContain('Regelverletzung');
    expect(text).toContain('deterministisch');
    expect(text).toContain('keine Finanzaktion');
  });

  it('runs profile, mapping, rules-only analysis, disposition and redacted export', () => {
    const fixture = TestBed.createComponent(BusinessControllingWorkbenchComponent);
    fixture.componentRef.setInput('projectId', 'project-a');
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.sourceRevisionId = 'source-a';
    component.revisionDigest = '1'.repeat(64);

    component.profileImport();
    component.confirmIdentityMapping();
    component.statisticsRequested = true;
    component.startRun();
    component.setDisposition(finding, 'confirmed');
    component.exportFindings();
    fixture.detectChanges();

    expect(api.profileImport).toHaveBeenCalled();
    expect(api.confirmMapping).toHaveBeenCalled();
    expect(api.startRun).toHaveBeenCalledWith(
      expect.anything(),
      'c'.repeat(64),
      false,
      true,
      '',
    );
    expect(component.findings[0].disposition).toBe('confirmed');
    expect(component.exportDigest).toBe('d'.repeat(64));
  });
});
