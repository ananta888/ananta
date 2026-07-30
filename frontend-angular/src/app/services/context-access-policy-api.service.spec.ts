import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import {
  CONTEXT_POLICY_ROUTE_CAPABILITIES,
  ContextAccessPolicyApiService,
  ContextPolicyContractError,
} from './context-access-policy-api.service';

describe('ContextAccessPolicyApiService contract', () => {
  let service: ContextAccessPolicyApiService;
  const core = {
    get: jasmine.createSpy(),
    post: jasmine.createSpy(),
  };

  beforeEach(() => {
    core.get.calls.reset();
    core.post.calls.reset();
    TestBed.configureTestingModule({
      providers: [
        ContextAccessPolicyApiService,
        { provide: HubApiCoreService, useValue: core },
      ],
    });
    service = TestBed.inject(ContextAccessPolicyApiService);
  });

  it('uses the real list route and preserves the authoritative project query', (done) => {
    core.get.and.returnValue(of({
      status: 'success',
      data: [{
        policy_id: 'policy-a',
        version: 3,
        project_id: 'project/a',
        scope: 'project',
        policy_json: { policy_id: 'policy-a', version: 3, scope: 'project', rules: [] },
      }],
    }));
    service.listPolicies('http://hub.test', 'project/a', 'token').subscribe((records) => {
      expect(records[0].policy_id).toBe('policy-a');
      expect(core.get).toHaveBeenCalledWith(
        'http://hub.test/api/context-policy/policies?project_id=project%2Fa',
        'http://hub.test',
        'token',
      );
      done();
    });
  });

  it('uses the real latest-detail route with an encoded server policy ID', (done) => {
    core.get.and.returnValue(of({
      status: 'success',
      data: {
        policy_id: 'policy/a',
        version: 2,
        scope: 'project',
        policy_json: { policy_id: 'policy/a', version: 2, scope: 'project', rules: [] },
      },
    }));
    service.getLatestPolicy('http://hub.test', 'policy/a', 'token').subscribe(() => {
      expect(core.get).toHaveBeenCalledWith(
        'http://hub.test/api/context-policy/policies/policy%2Fa/latest',
        'http://hub.test',
        'token',
      );
      done();
    });
  });

  it('maps validate without treating it as lint or preview', (done) => {
    core.post.and.returnValue(of({
      status: 'error',
      valid: false,
      errors: ['server validation error'],
    }));
    const policy = { policy_id: 'policy-a', version: 1, scope: 'project', rules: [] };
    service.validatePolicy('http://hub.test', policy, 'token').subscribe((result) => {
      expect(result.valid).toBeFalse();
      expect(result.errors).toEqual(['server validation error']);
      expect(core.post).toHaveBeenCalledWith(
        'http://hub.test/api/context-policy/validate',
        policy,
        'http://hub.test',
        'token',
      );
      done();
    });
  });

  it('declares absent lifecycle and grant routes unavailable', () => {
    for (const flow of ['draft', 'lint', 'preview', 'activate', 'revoke', 'rollback', 'presets', 'destinations', 'grant']) {
      expect(CONTEXT_POLICY_ROUTE_CAPABILITIES.find((item) => item.flow === flow)?.routeAvailable).toBeFalse();
    }
  });

  it('rejects a malformed successful snapshot before it reaches UI state', (done) => {
    core.get.and.returnValue(of({
      status: 'success',
      data: [{
        policy_id: 'policy-a',
        version: 1,
        project_id: 'project-a',
        scope: 'project',
        policy_json: {
          policy_id: 'policy-a',
          version: 1,
          scope: 'project',
          rules: [{ id: 'missing-description' }],
        },
      }],
    }));
    service.listPolicies('http://hub.test', 'project-a', 'token').subscribe({
      next: () => fail('expected fail-closed contract rejection'),
      error: (error) => {
        expect(error instanceof ContextPolicyContractError).toBeTrue();
        expect(error.status).toBe(422);
        done();
      },
    });
  });

  it('rejects unknown policy enum values in a 2xx snapshot', (done) => {
    core.get.and.returnValue(of({
      status: 'success',
      data: [{
        policy_id: 'policy-a',
        version: 1,
        project_id: 'project-a',
        scope: 'project',
        policy_json: {
          policy_id: 'policy-a',
          version: 1,
          scope: 'project',
          rules: [{
            id: 'rule-a',
            description: 'invalid server enum',
            sensitivity: 'locally_invented_sensitivity',
          }],
        },
      }],
    }));
    service.listPolicies('http://hub.test', 'project-a', 'token').subscribe({
      next: () => fail('expected enum rejection'),
      error: (error) => {
        expect(error.status).toBe(422);
        done();
      },
    });
  });

  it('rejects a malformed success envelope instead of treating it as an empty list', (done) => {
    core.get.and.returnValue(of({ status: 'success' }));
    service.listPolicies('http://hub.test', 'project-a', 'token').subscribe({
      next: () => fail('expected envelope rejection'),
      error: (error) => {
        expect(error.status).toBe(422);
        done();
      },
    });
  });
});
