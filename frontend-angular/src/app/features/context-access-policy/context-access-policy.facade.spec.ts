import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  ContextAccessPolicyApiService,
  ContextPolicyContractError,
} from '../../services/context-access-policy-api.service';
import {
  ContextAccessPolicyFacade,
  contextPolicyUiError,
} from './context-access-policy.facade';

describe('ContextAccessPolicyFacade', () => {
  const policyRecord = {
    policy_id: 'policy-a',
    version: 4,
    project_id: 'project-a',
    scope: 'project',
    policy: {
      policy_id: 'policy-a',
      version: 4,
      scope: 'project',
      rules: [{
        id: 'rule-a',
        description: 'Server rule',
        source_types: ['docs'],
        allowed_worker_kinds: ['native_ananta_worker'],
        denied_runtime_kinds: ['cloud_worker'],
        allowed_provider_locations: ['private'],
        allowed_model_scopes: ['private_remote'],
        read_allowed: true,
        write_allowed: false,
        redaction_required: true,
        reason_tags: ['server-tag'],
      }],
    },
    raw: {},
  };
  const api = {
    listPolicies: vi.fn().mockReturnValue(of([policyRecord])),
    getLatestPolicy: vi.fn().mockReturnValue(of(policyRecord)),
    validatePolicy: vi.fn().mockReturnValue(of({ status: 'success', valid: true, errors: [] })),
  };

  beforeEach(() => {
    api.listPolicies.mockReset();
    api.getLatestPolicy.mockReset();
    api.validatePolicy.mockReset();
    api.listPolicies.mockReturnValue(of([policyRecord]));
    api.getLatestPolicy.mockReturnValue(of(policyRecord));
    api.validatePolicy.mockReturnValue(of({ status: 'success', valid: true, errors: [] }));
    TestBed.configureTestingModule({
      providers: [
        ContextAccessPolicyFacade,
        { provide: ContextAccessPolicyApiService, useValue: api },
        { provide: AgentDirectoryService, useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'http://hub.test', token: 'token' }] } },
      ],
    });
  });

  it('does not call the Hub without an authoritative project context', () => {
    const facade = TestBed.inject(ContextAccessPolicyFacade);
    facade.initialize(null);
    expect(api.listPolicies).not.toHaveBeenCalled();
    expect(facade.managementAuthorized()).toBeFalse();
    expect(facade.listError()?.state).toBe('not-found');
  });

  it('uses only server-returned rules for the global matrix', () => {
    const facade = TestBed.inject(ContextAccessPolicyFacade);
    facade.initialize('project-a');
    const row = facade.matrixRows()[0];
    expect(row.workerKinds).toContain('native_ananta_worker');
    expect(row.runtimeKinds).toContain('cloud_worker');
    expect(row.operations).toContain('read_allowed=true');
    expect(row.operations).toContain('write_allowed=false');
    expect(row.reasonData).toContain('server-tag');
    expect(facade.managementAuthorized()).toBeTrue();
  });

  it('never loads detail for a client-invented policy ID', () => {
    const facade = TestBed.inject(ContextAccessPolicyFacade);
    facade.initialize('project-a');
    facade.loadLatest('forged-policy');
    expect(api.getLatestPolicy).not.toHaveBeenCalled();
  });

  it('removes confirmed management permission on a forbidden reload', () => {
    const facade = TestBed.inject(ContextAccessPolicyFacade);
    facade.initialize('project-a');
    expect(facade.managementAuthorized()).toBeTrue();
    api.listPolicies.mockReturnValue(throwError(() => ({ status: 403 })));
    facade.reload();
    expect(facade.managementAuthorized()).toBeFalse();
    expect(facade.listError()?.state).toBe('forbidden');
  });

  it('fails closed when a 2xx snapshot violates the runtime contract', () => {
    api.listPolicies.mockReturnValue(throwError(() => new ContextPolicyContractError()));
    const facade = TestBed.inject(ContextAccessPolicyFacade);
    facade.initialize('project-a');
    expect(facade.policies()).toEqual([]);
    expect(facade.managementAuthorized()).toBeFalse();
    expect(facade.listError()?.state).toBe('unprocessable');
    expect(facade.matrixRows()).toEqual([]);
  });
});

describe('contextPolicyUiError', () => {
  it('preserves a server reason code and offers conflict handling only for 409', () => {
    const conflict = contextPolicyUiError({
      status: 409,
      error: { reason_code: 'server_version_conflict', message: 'changed' },
    });
    expect(conflict.state).toBe('conflict');
    expect(conflict.reasonCode).toBe('server_version_conflict');
    expect(conflict.conflict).toBeTrue();
    expect(contextPolicyUiError({ status: 400 }).conflict).toBeFalse();
  });

  it('maps malformed successful snapshots to unprocessable without inventing a reason code', () => {
    const error = contextPolicyUiError(new ContextPolicyContractError());
    expect(error.state).toBe('unprocessable');
    expect(error.reasonCode).toBeUndefined();
    expect(error.conflict).toBeFalse();
  });

  it('covers every fail-closed lifecycle transport state', () => {
    const expected = new Map<number, string>([
      [0, 'offline'],
      [401, 'unauthorized'],
      [403, 'forbidden'],
      [404, 'not-found'],
      [409, 'conflict'],
      [422, 'unprocessable'],
      [429, 'rate-limited'],
      [500, 'server-error'],
    ]);
    for (const [status, state] of expected) {
      expect(contextPolicyUiError({ status }).state).withContext(String(status)).toBe(state);
    }
  });
});
