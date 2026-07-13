import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { OpsApiClient } from '../../services/ops-api.client';
import { GitOpsComponent } from './git-ops.component';

describe('GitOpsComponent', () => {
  const api = {
    listGitWorkspaces: vi.fn(() => of({ items: [{ workspace_id: 'repo', label: 'Ananta' }], count: 1 })),
    getGitStatus: vi.fn(() => of({ workspace_id: 'repo', branch: 'main', upstream: 'origin/main', remote_name: 'origin', ahead: 1, behind: 0, dirty: true, changed_files: [], recent_commits: [] })),
    getGitChanges: vi.fn(() => of({ items: [{ path: 'agent/app.py', index_status: 'M', staged: true }, { path: 'README.md', worktree_status: 'M', unstaged: true }], count: 2 })),
    getGitHistory: vi.fn(() => of({ items: [{ sha: 'abc123456789', subject: 'feat(ops): transparent git' }], count: 1 })),
    getGitBranches: vi.fn(() => of({ items: [{ name: 'main', current: true, upstream: 'origin/main' }], count: 1 })),
    getGitRemotes: vi.fn(() => of({ items: [{ name: 'origin', fetch_url: 'https://example.invalid/repo.git' }], count: 1 })),
    getGitActivity: vi.fn(() => of({ items: [{ id: '1', action: 'commit', summary: 'Ananta commit' }], count: 1 })),
    getGitDiff: vi.fn(() => of({ workspace_id: 'repo', path: 'agent/app.py', cached: true, diff: '+change', truncated: false })),
    stageGitPaths: vi.fn(() => of({ ok: true, action: 'stage' })),
    unstageGitPaths: vi.fn(() => of({ ok: true, action: 'unstage' })),
    discardGitPaths: vi.fn(() => of({ ok: true, action: 'discard' })),
    commitGit: vi.fn(() => of({ ok: true, action: 'commit' })),
    fetchGit: vi.fn(() => of({ ok: true, action: 'fetch' })),
    pullGit: vi.fn(() => of({ ok: true, action: 'pull' })),
    pushGit: vi.fn(() => of({ ok: true, action: 'push' })),
  };

  beforeEach(() => {
    Object.values(api).forEach((mock: any) => mock.mockClear());
    TestBed.configureTestingModule({ imports: [GitOpsComponent], providers: [{ provide: OpsApiClient, useValue: api }] });
  });

  it('loads status, changes, history and Ananta activity as separate read models', () => {
    const fixture = TestBed.createComponent(GitOpsComponent);
    fixture.componentRef.setInput('baseUrl', 'http://hub');
    fixture.detectChanges();

    const component = fixture.componentInstance;
    expect(component.changedFiles.map((file) => file.path)).toEqual(['agent/app.py', 'README.md']);
    expect(component.commits[0].subject).toContain('transparent git');
    expect(component.activity[0].summary).toBe('Ananta commit');
    expect(fixture.nativeElement.textContent).toContain('origin/main');
  });

  it('stages only explicitly selected unstaged paths', () => {
    const fixture = TestBed.createComponent(GitOpsComponent);
    const component = fixture.componentInstance;
    component.baseUrl = 'http://hub';
    component.changedFiles = [
      { path: 'already.py', staged: true },
      { path: 'selected.py', unstaged: true },
      { path: 'not-selected.py', unstaged: true },
    ];
    component.selectedPaths = new Set(['already.py', 'selected.py']);

    component.stageSelected();

    expect(api.stageGitPaths).toHaveBeenCalledWith('http://hub', 'repo', ['selected.py'], false, undefined);
  });

  it('requires an explicit second trigger after a digest-bound approval', () => {
    api.pushGit.mockReturnValueOnce(of({ ok: false, action: 'push', decision: 'approval_required', approval_id: 'APR-3' }));
    const fixture = TestBed.createComponent(GitOpsComponent);
    const component = fixture.componentInstance;
    component.baseUrl = 'http://hub';
    component.status = { workspace_id: 'repo', branch: 'main', upstream: 'origin/main', remote_name: 'origin', dirty: false, changed_files: [], recent_commits: [] };

    component.runSync('push', 'already-confirmed');

    expect(component.pendingApprovalId).toBe('APR-3');
    expect(component.retryAction?.approvalId).toBe('APR-3');
    expect(api.pushGit).toHaveBeenCalledTimes(1);
  });

  it('does not treat a rejected or mismatched approval id as a new approval request', () => {
    api.pushGit.mockReturnValueOnce(of({
      ok: false,
      action: 'push',
      decision: 'policy_denied',
      approval_id: 'APR-WRONG',
      error: { code: 'policy_denied', message: 'approval_digest_mismatch' },
    }));
    const fixture = TestBed.createComponent(GitOpsComponent);
    const component = fixture.componentInstance;
    component.baseUrl = 'http://hub';
    component.status = { workspace_id: 'repo', branch: 'main', upstream: 'origin/main', remote_name: 'origin', dirty: false, changed_files: [], recent_commits: [] };

    component.runSync('push', 'APR-WRONG');

    expect(component.pendingApprovalId).toBe('');
    expect(component.retryAction).toBeNull();
    expect(component.actionError).toBe(true);
  });
});
