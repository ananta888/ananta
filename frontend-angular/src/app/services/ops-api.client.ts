import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of, throwError } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import {
  ComposeProjectSummary,
  DockerContainerDetails,
  DockerContainerStats,
  DockerContainerSummary,
  DockerDiskUsage,
  DockerEngineStatus,
  DockerImageSummary,
  DockerInfo,
  DockerNetworkSummary,
  DockerVolumeSummary,
  GitBranchSummary,
  GitActivityEntry,
  GitCommitSummary,
  GitChangedFile,
  GitDiff,
  GitRemoteSummary,
  GitStatus,
  GitWorkspaceSummary,
  OpsActionResult,
  OpsTextResult,
} from '../features/operations/ops.models';

export * from '../features/operations/ops.models';

@Injectable({ providedIn: 'root' })
export class OpsApiClient {
  private core = inject(HubApiCoreService);

  listGitWorkspaces(baseUrl: string, token?: string): Observable<{ items: GitWorkspaceSummary[]; count: number }> {
    return this.core.get<{ items: GitWorkspaceSummary[]; count: number }>(`${baseUrl}/api/ops/git/workspaces`, baseUrl, token, true);
  }

  getGitStatus(baseUrl: string, workspaceId = 'repo', token?: string): Observable<GitStatus> {
    const q = new URLSearchParams({ workspace_id: workspaceId });
    return this.core.get<GitStatus>(`${baseUrl}/api/ops/git/status?${q.toString()}`, baseUrl, token, true);
  }

  getGitChanges(baseUrl: string, workspaceId = 'repo', token?: string): Observable<{ items: GitChangedFile[]; count: number }> {
    const q = new URLSearchParams({ workspace_id: workspaceId });
    return this.core.get<{ items: GitChangedFile[]; count: number }>(`${baseUrl}/api/ops/git/changes?${q.toString()}`, baseUrl, token, true);
  }

  getGitDiff(baseUrl: string, workspaceId = 'repo', options: { path?: string; cached?: boolean; scope?: 'unstaged' | 'staged' | 'combined' } = {}, token?: string): Observable<GitDiff> {
    const q = new URLSearchParams({ workspace_id: workspaceId });
    if (options.path) q.set('path', options.path);
    if (options.cached) q.set('cached', 'true');
    if (options.scope) q.set('scope', options.scope);
    return this.core.get<GitDiff>(`${baseUrl}/api/ops/git/diff?${q.toString()}`, baseUrl, token, false);
  }

  getGitHistory(baseUrl: string, workspaceId = 'repo', limit = 50, token?: string): Observable<{ items: GitCommitSummary[]; count: number }> {
    const q = new URLSearchParams({ workspace_id: workspaceId, limit: String(limit) });
    return this.core.get<{ items: GitCommitSummary[]; count: number }>(`${baseUrl}/api/ops/git/history?${q.toString()}`, baseUrl, token, true);
  }

  getGitBranches(baseUrl: string, workspaceId = 'repo', token?: string): Observable<{ items: GitBranchSummary[]; count: number }> {
    const q = new URLSearchParams({ workspace_id: workspaceId });
    return this.core.get<{ items: GitBranchSummary[]; count: number }>(`${baseUrl}/api/ops/git/branches?${q.toString()}`, baseUrl, token, true);
  }

  getGitRemotes(baseUrl: string, workspaceId = 'repo', token?: string): Observable<{ items: GitRemoteSummary[]; count: number }> {
    const q = new URLSearchParams({ workspace_id: workspaceId });
    return this.core.get<{ items: GitRemoteSummary[]; count: number }>(`${baseUrl}/api/ops/git/remotes?${q.toString()}`, baseUrl, token, true);
  }

  getGitActivity(baseUrl: string, workspaceId = 'repo', limit = 100, token?: string): Observable<{ items: GitActivityEntry[]; count: number }> {
    const q = new URLSearchParams({ workspace_id: workspaceId, limit: String(limit) });
    return this.core.get<{ items: GitActivityEntry[]; count: number }>(`${baseUrl}/api/ops/git/activity?${q.toString()}`, baseUrl, token, true);
  }

  stageGitPaths(baseUrl: string, workspaceId: string, paths: string[], unstage = false, approvalId?: string, token?: string): Observable<OpsActionResult> {
    return this.postAction(`${baseUrl}/api/ops/git/stage`, {
      workspace_id: workspaceId, paths, unstage, approval_id: approvalId || undefined,
    }, baseUrl, token);
  }

  unstageGitPaths(baseUrl: string, workspaceId: string, paths: string[], approvalId?: string, token?: string): Observable<OpsActionResult> {
    return this.postAction(`${baseUrl}/api/ops/git/unstage`, {
      workspace_id: workspaceId, paths, approval_id: approvalId || undefined,
    }, baseUrl, token);
  }

  discardGitPaths(baseUrl: string, workspaceId: string, paths: string[], approvalId?: string, token?: string): Observable<OpsActionResult> {
    return this.postAction(`${baseUrl}/api/ops/git/discard`, {
      workspace_id: workspaceId, paths, approval_id: approvalId || undefined,
    }, baseUrl, token);
  }

  commitGit(baseUrl: string, workspaceId: string, message: string, approvalId?: string, token?: string): Observable<OpsActionResult> {
    return this.postAction(`${baseUrl}/api/ops/git/commit`, {
      workspace_id: workspaceId, message, approval_id: approvalId || undefined,
    }, baseUrl, token);
  }

  fetchGit(baseUrl: string, workspaceId: string, options: { remote?: string; approvalId?: string } = {}, token?: string): Observable<OpsActionResult> {
    return this.postAction(`${baseUrl}/api/ops/git/fetch`, {
      workspace_id: workspaceId, remote: options.remote || undefined, approval_id: options.approvalId || undefined,
    }, baseUrl, token);
  }

  pullGit(baseUrl: string, workspaceId: string, options: { remote?: string; branch?: string; approvalId?: string } = {}, token?: string): Observable<OpsActionResult> {
    return this.postAction(`${baseUrl}/api/ops/git/pull`, {
      workspace_id: workspaceId, remote: options.remote || undefined, branch: options.branch || undefined, approval_id: options.approvalId || undefined,
    }, baseUrl, token);
  }

  pushGit(baseUrl: string, workspaceId: string, options: { remote?: string; branch?: string; approvalId?: string } = {}, token?: string): Observable<OpsActionResult> {
    return this.postAction(`${baseUrl}/api/ops/git/push`, {
      workspace_id: workspaceId, remote: options.remote || undefined, branch: options.branch || undefined, approval_id: options.approvalId || undefined,
    }, baseUrl, token);
  }

  getDockerStatus(baseUrl: string, token?: string): Observable<DockerEngineStatus> {
    return this.core.get<DockerEngineStatus>(`${baseUrl}/api/ops/docker/status`, baseUrl, token, true);
  }

  listDockerContainers(baseUrl: string, token?: string): Observable<{ items: DockerContainerSummary[]; count: number }> {
    return this.core.get<{ items: DockerContainerSummary[]; count: number }>(`${baseUrl}/api/ops/docker/containers`, baseUrl, token, true);
  }

  getDockerInfo(baseUrl: string, token?: string): Observable<DockerInfo> {
    return this.core.get<DockerInfo | { info: DockerInfo }>(`${baseUrl}/api/ops/docker/info`, baseUrl, token, true).pipe(
      map((value) => 'info' in value && value.info ? value.info : value as DockerInfo),
    );
  }

  listDockerImages(baseUrl: string, token?: string): Observable<{ items: DockerImageSummary[]; count: number }> {
    return this.core.get<{ items: DockerImageSummary[]; count: number }>(`${baseUrl}/api/ops/docker/images`, baseUrl, token, true);
  }

  listDockerNetworks(baseUrl: string, token?: string): Observable<{ items: DockerNetworkSummary[]; count: number }> {
    return this.core.get<{ items: DockerNetworkSummary[]; count: number }>(`${baseUrl}/api/ops/docker/networks`, baseUrl, token, true);
  }

  listDockerVolumes(baseUrl: string, token?: string): Observable<{ items: DockerVolumeSummary[]; count: number }> {
    return this.core.get<{ items: DockerVolumeSummary[]; count: number }>(`${baseUrl}/api/ops/docker/volumes`, baseUrl, token, true);
  }

  getDockerDiskUsage(baseUrl: string, token?: string): Observable<DockerDiskUsage> {
    return this.core.get<DockerDiskUsage | { items: Array<{ type?: string; total_count?: number | string; active?: number | string; size?: number | string; reclaimable?: number | string }> }>(`${baseUrl}/api/ops/docker/disk-usage`, baseUrl, token, true).pipe(
      map((value) => {
        if (!('items' in value) || !Array.isArray(value.items)) return value as DockerDiskUsage;
        const output: DockerDiskUsage = {};
        for (const row of value.items) {
          const key = String(row.type || '').toLowerCase().replace(/\s+/g, '_');
          const item = { count: row.total_count, active: row.active, size: row.size, reclaimable: row.reclaimable };
          if (key.includes('image')) output.images = item;
          else if (key.includes('container')) output.containers = item;
          else if (key.includes('volume')) output.volumes = item;
          else if (key.includes('build')) output.build_cache = item;
        }
        return output;
      }),
    );
  }

  inspectDockerContainer(baseUrl: string, containerId: string, token?: string): Observable<DockerContainerDetails> {
    return this.core.get<DockerContainerDetails>(`${baseUrl}/api/ops/docker/containers/${encodeURIComponent(containerId)}/inspect`, baseUrl, token, false);
  }

  getDockerContainerStats(baseUrl: string, containerId: string, token?: string): Observable<DockerContainerStats> {
    return this.core.get<DockerContainerStats | { stats: DockerContainerStats }>(`${baseUrl}/api/ops/docker/containers/${encodeURIComponent(containerId)}/stats`, baseUrl, token, false).pipe(
      map((value) => 'stats' in value && value.stats ? value.stats : value as DockerContainerStats),
    );
  }

  getDockerContainerLogs(baseUrl: string, containerId: string, tail = 200, token?: string): Observable<OpsTextResult> {
    const q = new URLSearchParams({ tail: String(tail) });
    return this.core.get<OpsTextResult>(`${baseUrl}/api/ops/docker/containers/${encodeURIComponent(containerId)}/logs?${q.toString()}`, baseUrl, token, false);
  }

  runDockerContainerAction(baseUrl: string, containerId: string, action: 'start' | 'stop' | 'restart', approvalId?: string, token?: string): Observable<OpsActionResult> {
    return this.postAction(`${baseUrl}/api/ops/docker/containers/${encodeURIComponent(containerId)}/action`, {
      action, approval_id: approvalId || undefined,
    }, baseUrl, token);
  }

  listComposeProjects(baseUrl: string, token?: string): Observable<{ items: ComposeProjectSummary[]; count: number }> {
    return this.core.get<{ items: ComposeProjectSummary[]; count: number }>(`${baseUrl}/api/ops/compose/projects`, baseUrl, token, true);
  }

  getComposeProjectStatus(baseUrl: string, projectId: string, token?: string): Observable<ComposeProjectSummary> {
    return this.core.get<ComposeProjectSummary>(`${baseUrl}/api/ops/compose/projects/${encodeURIComponent(projectId)}/status`, baseUrl, token, true);
  }


  getComposeProjectConfig(baseUrl: string, projectId: string, token?: string): Observable<OpsTextResult> {
    return this.core.get<OpsTextResult>(`${baseUrl}/api/ops/compose/projects/${encodeURIComponent(projectId)}/config`, baseUrl, token, false);
  }

  getComposeProjectLogs(baseUrl: string, projectId: string, service?: string, tail = 200, token?: string): Observable<OpsTextResult> {
    const q = new URLSearchParams({ tail: String(tail) });
    if (service) q.set('service', service);
    return this.core.get<OpsTextResult>(`${baseUrl}/api/ops/compose/projects/${encodeURIComponent(projectId)}/logs?${q.toString()}`, baseUrl, token, false);
  }

  runComposeProjectAction(baseUrl: string, projectId: string, action: 'pull' | 'up' | 'stop' | 'down' | 'restart', service?: string, approvalId?: string, token?: string): Observable<OpsActionResult> {
    return this.postAction(`${baseUrl}/api/ops/compose/projects/${encodeURIComponent(projectId)}/action`, {
      action, service: service || undefined, approval_id: approvalId || undefined,
    }, baseUrl, token);
  }

  private postAction(url: string, body: Record<string, unknown>, baseUrl: string, token?: string): Observable<OpsActionResult> {
    return this.core.post<OpsActionResult>(url, body, baseUrl, token).pipe(
      catchError((error) => {
        let current = error?.error;
        for (let i = 0; i < 4; i += 1) {
          if (current && typeof current === 'object' && 'data' in current) current = current.data;
          else break;
        }
        if (current && typeof current === 'object' && ('approval_id' in current || 'decision' in current || 'error' in current)) {
          return of(current as OpsActionResult);
        }
        return throwError(() => error);
      }),
    );
  }
}
