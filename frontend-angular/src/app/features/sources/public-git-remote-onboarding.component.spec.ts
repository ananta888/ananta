import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';

import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { PublicGitRemoteOnboardingComponent } from './public-git-remote-onboarding.component';

describe('PublicGitRemoteOnboardingComponent', () => {
  let fixture: ComponentFixture<PublicGitRemoteOnboardingComponent>;
  let component: PublicGitRemoteOnboardingComponent;
  const validatePublicRemote = vi.fn();
  const createPublicRemote = vi.fn();
  const api = { validatePublicRemote, createPublicRemote };

  const validation = {
    validation_handle: 'public-validation-example',
    provider: 'github_public' as const,
    requested_ref: 'main',
    commit_sha: '0123456789abcdef0123456789abcdef01234567',
    expires_at_epoch: 2_000_000_000,
    capabilities: { create_remote: true },
  };
  const creation = {
    remote_id: 'remote-public-example',
    provider: 'github_public' as const,
    commit_sha: validation.commit_sha,
    state: 'active',
    capabilities: { refresh: true },
  };

  beforeEach(async () => {
    vi.clearAllMocks();
    validatePublicRemote.mockReturnValue(of(validation));
    createPublicRemote.mockReturnValue(of(creation));
    await TestBed.configureTestingModule({
      imports: [PublicGitRemoteOnboardingComponent],
      providers: [
        { provide: SourceControlV1GovernanceApiClient, useValue: api },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(PublicGitRemoteOnboardingComponent);
    component = fixture.componentInstance;
    component.projectId = 'project-alpha';
    fixture.detectChanges();
  });

  it('validates structured GitHub coordinates and creates only from the returned handle', () => {
    const emitted = vi.fn();
    component.remoteCreated.subscribe(emitted);
    component.repositoryUrl.set('https://github.com/openai/public-repository');
    component.requestedRef.set('main');

    component.submit();

    expect(validatePublicRemote).toHaveBeenCalledWith(
      'project-alpha',
      {
        provider: 'github_public',
        owner: 'openai',
        repository: 'public-repository',
        requested_ref: 'main',
      },
    );
    expect(createPublicRemote).toHaveBeenCalledWith(
      'project-alpha',
      'public-validation-example',
      expect.stringMatching(/^ui:public-remote:create:/),
    );
    const serialized = JSON.stringify(validatePublicRemote.mock.calls[0]?.[0]);
    expect(serialized).not.toContain('clone_url');
    expect(serialized).not.toContain('token');
    expect(serialized).not.toContain('credential');
    expect(serialized).not.toContain('port');
    expect(emitted).toHaveBeenCalledWith(creation);
  });

  it('accepts a public DNS host as a structured HTTPS-Git coordinate', () => {
    component.repositoryUrl.set('https://git.example.org/team/repository.git');
    component.requestedRef.set('refs/heads/main');

    component.submit();

    expect(validatePublicRemote).toHaveBeenCalledWith(
      'project-alpha',
      {
        provider: 'https_git',
        host: 'git.example.org',
        repository: 'team/repository.git',
        requested_ref: 'refs/heads/main',
      },
    );
  });

  it('fails closed without a route project context', () => {
    component.projectId = '';
    component.repositoryUrl.set('https://github.com/openai/public-repository');

    component.submit();

    expect(validatePublicRemote).not.toHaveBeenCalled();
    expect(createPublicRemote).not.toHaveBeenCalled();
    expect(component.canSubmit()).toBeFalsy();
  });

  it.each([
    'https://127.0.0.1/team/repository.git',
    'https://10.0.0.1/team/repository.git',
    'https://[::1]/team/repository.git',
    'https://git.example.org:443/team/repository.git',
    'http://git.example.org/team/repository.git',
    'https://localhost/team/repository.git',
    'https://token@git.example.org/team/repository.git',
  ])('rejects private, credentialed or non-canonical repository URLs: %s', (url) => {
    component.repositoryUrl.set(url);

    component.submit();

    expect(validatePublicRemote).not.toHaveBeenCalled();
    expect(createPublicRemote).not.toHaveBeenCalled();
  });
});
