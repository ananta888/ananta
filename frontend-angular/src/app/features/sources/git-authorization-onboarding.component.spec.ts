import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';

import type { SourceControlGitAuthorizationView } from '../../models/source-control-v1-governance.model';
import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { GitAuthorizationOnboardingComponent } from './git-authorization-onboarding.component';

describe('GitAuthorizationOnboardingComponent', () => {
  let fixture: ComponentFixture<GitAuthorizationOnboardingComponent>;
  let component: GitAuthorizationOnboardingComponent;
  const gitAuthorizationHealth = vi.fn();
  const validateGitAuthorization = vi.fn();
  const provisionGitAuthorization = vi.fn();
  const listGitAuthorizations = vi.fn();
  const gitAuthorizationDetail = vi.fn();
  const revokeGitAuthorization = vi.fn();
  const recordGitAuthorizationScopeLoss = vi.fn();
  const api = {
    gitAuthorizationHealth,
    validateGitAuthorization,
    provisionGitAuthorization,
    listGitAuthorizations,
    gitAuthorizationDetail,
    revokeGitAuthorization,
    recordGitAuthorizationScopeLoss,
  };

  const validation: SourceControlGitAuthorizationView = {
    authorization_ref: 'auth-example',
    authorization_kind: 'github_app',
    repository: 'team/repository',
    authorization_state: 'active',
    granted_scopes: ['contents:read'],
    credential_configured: true,
    persisted: false,
    current_revision: 0,
    etag: null,
    next_actions: [],
  };
  const persisted: SourceControlGitAuthorizationView = {
    ...validation,
    persisted: true,
    current_revision: 1,
    etag: '"git-auth-v1:1"',
    next_actions: ['revoke', 'record_scope_loss'],
  };

  beforeEach(async () => {
    vi.clearAllMocks();
    gitAuthorizationHealth.mockReturnValue(of({
      status: 'healthy',
      reason_code: null,
      provider_status: 'healthy',
      connector_ready: { github_repository: true, generic_git: true },
      registration_count: 0,
      active_registration_count: 0,
    }));
    listGitAuthorizations.mockReturnValue(of({ items: [], next_cursor: null }));
    validateGitAuthorization.mockReturnValue(of(validation));
    provisionGitAuthorization.mockReturnValue(of(persisted));
    gitAuthorizationDetail.mockReturnValue(of(persisted));
    revokeGitAuthorization.mockReturnValue(of({
      ...persisted,
      authorization_state: 'revoked',
      current_revision: 2,
      etag: '"git-auth-v1:2"',
      next_actions: [],
    }));
    recordGitAuthorizationScopeLoss.mockReturnValue(of({
      ...persisted,
      authorization_state: 'scope_loss',
      current_revision: 2,
      etag: '"git-auth-v1:2"',
      next_actions: [],
    }));

    await TestBed.configureTestingModule({
      imports: [GitAuthorizationOnboardingComponent],
      providers: [
        { provide: SourceControlV1GovernanceApiClient, useValue: api },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(GitAuthorizationOnboardingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('validates and provisions with only the public selection DTO', () => {
    const emitted = vi.fn();
    component.provisioned.subscribe(emitted);
    component.authorizationHandle.set('github-installation-primary');
    component.authorizationKind.set('github_app');
    component.repository.set('team/repository');

    component.provision();

    const selection = {
      authorization_handle: 'github-installation-primary',
      authorization_kind: 'github_app',
      repository: 'team/repository',
    };
    expect(validateGitAuthorization).toHaveBeenCalledWith(selection);
    expect(provisionGitAuthorization).toHaveBeenCalledWith(
      selection,
      expect.stringMatching(/^ui:git-authorization:create:/),
    );
    const serialized = JSON.stringify(provisionGitAuthorization.mock.calls[0]?.[0]);
    expect(serialized).not.toContain('token');
    expect(serialized).not.toContain('clone_url');
    expect(serialized).not.toContain('credential_ref');
    expect(emitted).toHaveBeenCalledWith(persisted);
  });

  it('fails closed when provider health is unavailable', () => {
    component.health.set({
      status: 'unavailable',
      reason_code: 'provider_unavailable',
      provider_status: 'unavailable',
      connector_ready: { github_repository: false, generic_git: false },
      registration_count: 0,
      active_registration_count: 0,
    });
    component.authorizationHandle.set('github-installation-primary');
    component.repository.set('team/repository');

    component.provision();

    expect(validateGitAuthorization).not.toHaveBeenCalled();
    expect(provisionGitAuthorization).not.toHaveBeenCalled();
  });

  it('uses the quoted revision ETag for revoke and scope-loss transitions', () => {
    component.revoke(persisted);
    component.recordScopeLoss(persisted);

    expect(revokeGitAuthorization).toHaveBeenCalledWith(
      'auth-example',
      'team/repository',
      {
        etag: '"git-auth-v1:1"',
        idempotencyKey: expect.stringMatching(/^ui:git-authorization:revoke:/),
      },
    );
    expect(recordGitAuthorizationScopeLoss).toHaveBeenCalledWith(
      'auth-example',
      'team/repository',
      {
        etag: '"git-auth-v1:1"',
        idempotencyKey: expect.stringMatching(/^ui:git-authorization:scope-loss:/),
      },
    );
  });
});
