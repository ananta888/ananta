import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { firstValueFrom, of, throwError } from 'rxjs';

import { PolicyService } from './policy.service';
import { SourceControlV1ApiClient } from '../../../services/source-control-v1-api.client';
import { SourceControlAccessDecisionKind } from '../../../models/source-control-v1-api.model';

function mockSourceControl() {
  return {
    listContextPolicies: vi.fn(() => of({
      items: [{
        policy_id: 'policy-1',
        latest_version: 1,
        state: 'active',
        etag: 'active:1',
        policy_digest: 'a'.repeat(64),
      }],
      next_cursor: null,
    })),
    getActiveContextPolicy: vi.fn(() => of({
      policy: {
        policy_id: 'policy-1',
        version: 1,
        tenant_id: 't1',
        project_id: 'p1',
        state: 'active',
        document: {},
        policy_digest: 'b'.repeat(64),
        etag: 'active:1',
        created_by: 'test',
        created_at: new Date().toISOString(),
      },
      etag: 'active:1',
    })),
    previewAccess: vi.fn(() => of({
      schema: 'ananta.source-control.access-decision.v1',
      source_revision_id: 'src',
      revision_digest: 'rev',
      destination_id: 'dst',
      operation: 'tool_write',
      transformation: 'redacted',
      purpose: 'code_navigation',
      decision: 'allow',
      reason_codes: ['allow'],
      matched_rule_path: ['r1'],
      default_applied: false,
      approval_requirement: null,
      policy_digest: 'c'.repeat(64),
    })),
    loadAccessMatrix: vi.fn(() => of({
      items: [],
      source_next_cursor: null,
      destination_next_cursor: null,
    })),
  };
}

function setup() {
  const sourceControlApi = mockSourceControl();
  TestBed.configureTestingModule({
    providers: [
      PolicyService,
      { provide: SourceControlV1ApiClient, useValue: sourceControlApi },
    ],
  });
  return { svc: TestBed.inject(PolicyService), sourceControlApi };
}

describe('PolicyService — Audit + Risk + RateLimit', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('appendAudit adds entry and respects limit', () => {
    const { svc } = setup();
    for (let i = 0; i < 5; i++) {
      svc.appendAudit({ kind: 'tool-call', action: `act-${i}` });
    }
    expect(svc.auditLog().length).toBe(5);
    expect(svc.auditLog()[0].action).toBe('act-4');
  });

  it('clearAudit empties the list', () => {
    const { svc } = setup();
    svc.appendAudit({ kind: 'tool-call', action: 'x' });
    svc.clearAudit();
    expect(svc.auditLog().length).toBe(0);
  });

  it('assessToolRisk: low for harmless tool', () => {
    const { svc } = setup();
    const r = svc.assessToolRisk('help', { topic: 'greeting' });
    expect(r.level).toBe('low');
    expect(r.recommendation).toBe('allow');
  });

  it('assessToolRisk: high for write_file', () => {
    const { svc } = setup();
    const r = svc.assessToolRisk('write_file', { path: '/x.py' });
    expect(r.level).toBe('high');
    expect(r.recommendation).toBe('require_approval');
  });

  it('assessToolRisk: medium for read_file', () => {
    const { svc } = setup();
    const r = svc.assessToolRisk('read_file', { path: '/x.py' });
    expect(r.level).toBe('medium');
    expect(r.recommendation).toBe('warn');
  });

  it('assessToolRisk: critical for destructive pattern', () => {
    const { svc } = setup();
    const r = svc.assessToolRisk('shell_exec', { cmd: 'rm -rf /' });
    expect(r.level).toBe('critical');
    expect(r.recommendation).toBe('deny');
  });

  it('checkRate: counts requests and enforces limit', () => {
    const { svc } = setup();
    const r1 = svc.checkRate('k1', 2);
    expect(r1.allowed).toBe(true);
    svc.checkRate('k1', 2);
    const r3 = svc.checkRate('k1', 2);
    expect(r3.allowed).toBe(false);
    expect(r3.remaining).toBe(0);
  });

  it('checkRate: separate buckets are independent', () => {
    const { svc } = setup();
    svc.checkRate('k1', 1);
    expect(svc.checkRate('k1', 1).allowed).toBe(false);
    expect(svc.checkRate('k2', 1).allowed).toBe(true);
  });

  it('resetRate: removes bucket', () => {
    const { svc } = setup();
    svc.checkRate('k1', 1);
    svc.checkRate('k1', 1);
    expect(svc.checkRate('k1', 1).allowed).toBe(false);
    svc.resetRate('k1');
    expect(svc.checkRate('k1', 1).allowed).toBe(true);
  });

  it('throws validation_error for missing action binding input', async () => {
    const { svc, sourceControlApi } = setup();
    sourceControlApi.previewAccess.mockReturnValue(of({
      schema: 'ananta.source-control.access-decision.v1',
      source_revision_id: 'src',
      revision_digest: 'rev',
      destination_id: 'dst',
      operation: 'tool_write',
      transformation: 'redacted',
      purpose: 'code_navigation',
      decision: 'allow',
      reason_codes: ['allow'],
      matched_rule_path: ['r1'],
      default_applied: false,
      approval_requirement: null,
      policy_digest: 'c'.repeat(64),
    }));

    await expect(
      firstValueFrom(svc.checkAction({ actionType: 'tool_write' })),
    ).rejects.toBeTruthy();
  });

  it.each(['unsupported', 'mystery'] as const)(
    'maps unsupported backend decision %s to deny',
    async decision => {
      const { svc, sourceControlApi } = setup();
      sourceControlApi.previewAccess.mockReturnValue(of({
        schema: 'ananta.source-control.access-decision.v1',
        source_revision_id: 'src',
        revision_digest: 'rev',
        destination_id: 'dst',
        operation: 'tool_write',
        transformation: 'redacted',
        purpose: 'code_navigation',
        decision: decision as unknown as SourceControlAccessDecisionKind,
        reason_codes: [],
        matched_rule_path: [],
        default_applied: true,
        approval_requirement: null,
        policy_digest: 'c'.repeat(64),
      }));

      const result = await firstValueFrom(
        svc.checkAction({
          actionType: 'tool_write',
          sourceRevisionId: 'src',
          destinationId: 'dst',
          transformation: 'redacted',
          purpose: 'code_navigation',
        }),
      );

      expect(result.decision).toBe('deny');
      expect(result.reason).toBe('no_reason_code');
    },
  );

  it('preserves an explicit backend allow decision', async () => {
    const { svc, sourceControlApi } = setup();
    sourceControlApi.previewAccess.mockReturnValue(of({
      schema: 'ananta.source-control.access-decision.v1',
      source_revision_id: 'src',
      revision_digest: 'rev',
      destination_id: 'dst',
      operation: 'tool_read',
      transformation: 'redacted',
      purpose: 'code_navigation',
      decision: 'allow',
      reason_codes: ['policy_rule'],
      matched_rule_path: ['rule-a'],
      default_applied: false,
      approval_requirement: null,
      policy_digest: 'c'.repeat(64),
    }));

    const result = await firstValueFrom(
      svc.checkAction({
        actionType: 'tool_read',
        sourceRevisionId: 'src',
        destinationId: 'dst',
        transformation: 'redacted',
        purpose: 'code_navigation',
      }),
    );

    expect(result.decision).toBe('allow');
    expect(result.reason).toBe('policy_rule');
  });

  it.each([401, 403, 404, 409, 422, 429, 500, 503])(
    'does not replace policy load failure %s with a local success snapshot',
    async status => {
      const { svc, sourceControlApi } = setup();
      sourceControlApi.listContextPolicies = vi.fn(() => throwError(() => ({ status })));

      await expect(firstValueFrom(svc.loadCurrentSnapshot())).rejects.toBeInstanceOf(Error);
      expect(svc.getCachedSnapshot()).toBeNull();
    },
  );
});
