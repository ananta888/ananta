import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { SourceControlV1ApiClient } from './source-control-v1-api.client';
import {
  CONTEXT_POLICY_ROUTE_CAPABILITIES,
  ContextAccessPolicyApiService,
  ContextPolicyContractError,
} from './context-access-policy-api.service';

function mockSourceControl() {
  return {
    listContextPolicies: vi.fn(),
    listContextPolicyVersions: vi.fn(),
    getContextPolicyVersion: vi.fn(),
  };
}

function policyVersion(overrides: Record<string, unknown> = {}) {
  return {
    policy_id: 'policy-a',
    version: 1,
    project_id: 'project-a',
    state: 'active',
    created_at: '2026-01-01T00:00:00Z',
    policy_digest: 'policy-digest-a',
    etag: 'policy-etag-a',
    document: {
      policy_id: 'policy-a',
      version: 1,
      scope: 'project',
      rules: [{
        id: 'rule-a',
        description: 'Server rule',
      }],
    },
    ...overrides,
  };
}

describe('ContextAccessPolicyApiService contract', () => {
  let service: ContextAccessPolicyApiService;
  let sourceControl: ReturnType<typeof mockSourceControl>;

  beforeEach(() => {
    sourceControl = mockSourceControl();
    sourceControl.listContextPolicies.mockReset();
    sourceControl.listContextPolicyVersions.mockReset();
    sourceControl.getContextPolicyVersion.mockReset();
    TestBed.configureTestingModule({
      providers: [
        ContextAccessPolicyApiService,
        { provide: SourceControlV1ApiClient, useValue: sourceControl },
      ],
    });
    service = TestBed.inject(ContextAccessPolicyApiService);
  });

  it('resolves canonical policy snapshots from the latest summary IDs', (done) => {
    sourceControl.listContextPolicies.mockReturnValue(of({
      items: [{ policy_id: 'policy-a', latest_version: 3, project_id: 'project/a' }],
      next_cursor: null,
    }));
    sourceControl.getContextPolicyVersion.mockReturnValue(of({
      policy: policyVersion({ version: 3 }),
    }));

    service.listPolicies('http://hub.test', 'project/a', 'token').subscribe((records) => {
      expect(records).toHaveLength(1);
      expect(records[0].policy_id).toBe('policy-a');
      expect(records[0].project_id).toBe('project/a');
      expect(records[0].version).toBe(3);
      expect(sourceControl.listContextPolicies).toHaveBeenCalledWith({ limit: 200 });
      expect(sourceControl.getContextPolicyVersion).toHaveBeenCalledWith('policy-a', 3);
      done();
    });
  });

  it('resolves latest detail from versions listing', (done) => {
    sourceControl.listContextPolicyVersions.mockReturnValue(of({
      items: [
        { ...policyVersion({ policy_id: 'policy/a', version: 1, document: { ...policyVersion().document, version: 1, policy_id: 'policy/a' } }), },
        { ...policyVersion({ policy_id: 'policy/a', version: 2, document: { ...policyVersion().document, version: 2, policy_id: 'policy/a' } }), },
      ],
      next_cursor: null,
    }));
    service.getLatestPolicy('http://hub.test', 'policy/a', 'token').subscribe(() => {
      expect(sourceControl.listContextPolicyVersions).toHaveBeenCalledWith('policy/a', { limit: 200 });
      done();
    });
  });

  it('maps validate into validation error because remote validate endpoint is disabled in this workflow', (done) => {
    const policy = policyVersion().document;
    service.validatePolicy('http://hub.test', policy, 'token').subscribe({
      next: () => done(new Error('expected validation to fail closed')),
      error: (error) => {
        expect(error instanceof ContextPolicyContractError).toBeTruthy();
        expect(error.status).toBe(422);
        expect(sourceControl.listContextPolicies).not.toHaveBeenCalled();
        done();
      },
    });
  });

  it('declares absent lifecycle and grant routes unavailable', () => {
    // Only these four have no v1 route. The lifecycle flows do — the Hub
    // registers context_policy_draft, _lint, _preview, _activate, _revoke and
    // _rollback in agent/routes/source_control_v1.py — so asserting them absent
    // contradicted both the backend and the capability table this reads.
    for (const flow of ['validate', 'presets', 'destinations', 'grant']) {
      expect(CONTEXT_POLICY_ROUTE_CAPABILITIES.find((item) => item.flow === flow)?.routeAvailable).toBe(false);
    }
  });

  it('declares the lifecycle routes the Hub actually serves as available', () => {
    for (const flow of ['list', 'detail', 'create', 'version', 'draft', 'lint', 'preview', 'activate', 'revoke', 'rollback']) {
      expect(CONTEXT_POLICY_ROUTE_CAPABILITIES.find((item) => item.flow === flow)?.routeAvailable).toBe(true);
    }
  });

  it('rejects a malformed success snapshot before it reaches UI state', (done) => {
    sourceControl.listContextPolicies.mockReturnValue(of({
      items: [{ policy_id: 'policy-a', latest_version: 1, project_id: 'project-a' }],
      next_cursor: null,
    }));
    sourceControl.getContextPolicyVersion.mockReturnValue(of({
      policy: {
        ...policyVersion({ version: 1 }),
        document: {
          policy_id: 'policy-a',
          version: 1,
          scope: 'project',
          rules: [{ id: 'missing-description' }],
        },
      },
    }));
    service.listPolicies('http://hub.test', 'project-a', 'token').subscribe({
      next: () => done(new Error('expected fail-closed contract rejection')),
      error: (error) => {
        expect(error instanceof ContextPolicyContractError).toBe(true);
        expect(error.status).toBe(422);
        done();
      },
    });
  });

  it('rejects unknown policy enum values in a 2xx snapshot', (done) => {
    sourceControl.listContextPolicies.mockReturnValue(of({
      items: [{ policy_id: 'policy-a', latest_version: 1, project_id: 'project-a' }],
      next_cursor: null,
    }));
    sourceControl.getContextPolicyVersion.mockReturnValue(of({
      policy: {
        ...policyVersion({ version: 1 }),
        document: {
          policy_id: 'policy-a',
          version: 1,
          scope: 'project',
          rules: [{
            id: 'rule-a',
            description: 'invalid server enum',
            sensitivity: 'locally_invented_sensitivity',
          }],
        },
      },
    }));
    service.listPolicies('http://hub.test', 'project-a', 'token').subscribe({
      next: () => done(new Error('expected enum rejection')),
      error: (error) => {
        expect(error.status).toBe(422);
        done();
      },
    });
  });
});
