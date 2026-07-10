import { Injectable, inject } from '@angular/core';
import { Observable, catchError, of } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

export interface OpsError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface GitChangedFile {
  path: string;
  index_status?: string;
  worktree_status?: string;
  staged?: boolean;
  unstaged?: boolean;
  untracked?: boolean;
}

export interface GitStatus {
  workspace_id: string;
  branch: string;
  upstream: string;
  remote_name: string;
  dirty: boolean;
  changed_files: GitChangedFile[];
  recent_commits: Array<{ sha: string; subject: string }>;
  error?: OpsError | null;
}

export interface DockerEngineStatus {
  available: boolean;
  boundary: string;
  docker_version: string;
  compose_available: boolean;
  platform_hint: string;
  error?: OpsError | null;
}

export interface DockerContainerSummary {
  id: string;
  name: string;
  image: string;
  status: string;
  health?: string;
  ports?: string;
  compose_project?: string;
  uptime?: string;
}

export interface ComposeProjectSummary {
  project_id: string;
  name: string;
  project_directory: string;
  compose_files: string[];
  profiles: string[];
  marker: string;
  category: string;
  services: Array<{ name: string; state: string; health?: string; exit_code?: string; ports?: string }>;
  error?: OpsError | null;
}

@Injectable({ providedIn: 'root' })
export class OpsApiClient {
  private core = inject(HubApiCoreService);

  getGitStatus(baseUrl: string, workspaceId = 'repo', token?: string): Observable<GitStatus> {
    const q = new URLSearchParams({ workspace_id: workspaceId });
    return this.core.get<GitStatus>(`${baseUrl}/api/ops/git/status?${q.toString()}`, baseUrl, token, true);
  }

  getDockerStatus(baseUrl: string, token?: string): Observable<DockerEngineStatus> {
    return this.core.get<DockerEngineStatus>(`${baseUrl}/api/ops/docker/status`, baseUrl, token, true).pipe(
      catchError((err) => of(err?.error?.data as DockerEngineStatus))
    );
  }

  listDockerContainers(baseUrl: string, token?: string): Observable<{ items: DockerContainerSummary[]; count: number }> {
    return this.core.get<{ items: DockerContainerSummary[]; count: number }>(`${baseUrl}/api/ops/docker/containers`, baseUrl, token, true);
  }

  listComposeProjects(baseUrl: string, token?: string): Observable<{ items: ComposeProjectSummary[]; count: number }> {
    return this.core.get<{ items: ComposeProjectSummary[]; count: number }>(`${baseUrl}/api/ops/compose/projects`, baseUrl, token, true);
  }

  getComposeProjectStatus(baseUrl: string, projectId: string, token?: string): Observable<ComposeProjectSummary> {
    return this.core.get<ComposeProjectSummary>(`${baseUrl}/api/ops/compose/projects/${encodeURIComponent(projectId)}/status`, baseUrl, token, true);
  }
}
