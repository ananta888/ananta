import { TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { firstValueFrom, of, throwError } from 'rxjs';
import { vi } from 'vitest';
import { HubApiCoreService } from './hub-api-core.service';
import { OpsApiClient } from './ops-api.client';

describe('OpsApiClient', () => {
  const core = {
    get: vi.fn(() => of({ items: [], count: 0 })),
    post: vi.fn(() => of({ ok: true, action: 'stage' })),
  };

  beforeEach(() => {
    core.get.mockReset().mockReturnValue(of({ items: [], count: 0 }));
    core.post.mockReset().mockReturnValue(of({ ok: true, action: 'stage' }));
    TestBed.configureTestingModule({ providers: [OpsApiClient, { provide: HubApiCoreService, useValue: core }] });
  });

  it('builds a scoped staged diff request without leaking absolute paths', async () => {
    core.get.mockReturnValue(of({ workspace_id: 'repo', path: 'agent/app.py', scope: 'staged', cached: true, diff: '', truncated: false }));
    const api = TestBed.inject(OpsApiClient);

    await firstValueFrom(api.getGitDiff('http://hub', 'repo', { path: 'agent/app.py', scope: 'staged', cached: true }));

    const url = core.get.mock.calls[0][0] as string;
    expect(url).toContain('/api/ops/git/diff?');
    expect(url).toContain('workspace_id=repo');
    expect(url).toContain('path=agent%2Fapp.py');
    expect(url).toContain('scope=staged');
  });

  it('sends explicit paths and approval binding for git mutations', async () => {
    const api = TestBed.inject(OpsApiClient);

    await firstValueFrom(api.unstageGitPaths('http://hub', 'repo', ['agent/app.py'], 'APR-1'));

    expect(core.post).toHaveBeenCalledWith(
      'http://hub/api/ops/git/unstage',
      { workspace_id: 'repo', paths: ['agent/app.py'], approval_id: 'APR-1' },
      'http://hub',
      undefined,
    );
  });

  it('turns an approval-required HTTP response into an actionable result', async () => {
    core.post.mockReturnValue(throwError(() => new HttpErrorResponse({
      status: 409,
      error: { status: 'error', data: { ok: false, action: 'push', decision: 'approval_required', approval_id: 'APR-2' } },
    })));
    const api = TestBed.inject(OpsApiClient);

    const result = await firstValueFrom(api.pushGit('http://hub', 'repo'));

    expect(result.approval_id).toBe('APR-2');
    expect(result.decision).toBe('approval_required');
  });

  it('targets registered docker and compose resources by encoded id', async () => {
    const api = TestBed.inject(OpsApiClient);

    await firstValueFrom(api.runDockerContainerAction('http://hub', 'ananta/hub', 'restart'));
    await firstValueFrom(api.runComposeProjectAction('http://hub', 'stack/dev', 'restart', 'hub'));

    expect(core.post.mock.calls[0][0]).toBe('http://hub/api/ops/docker/containers/ananta%2Fhub/action');
    expect(core.post.mock.calls[1][0]).toBe('http://hub/api/ops/compose/projects/stack%2Fdev/action');
    expect(core.post.mock.calls[1][1]).toMatchObject({ action: 'restart', service: 'hub' });
  });
});

