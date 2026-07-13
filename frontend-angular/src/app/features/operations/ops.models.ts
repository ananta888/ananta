export interface OpsError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface OpsActionResult {
  ok: boolean;
  action: string;
  target_id?: string;
  decision?: 'allow' | 'approval_required' | 'policy_denied' | string;
  approval_id?: string | null;
  audit_ref?: string | null;
  metadata?: Record<string, unknown>;
  error?: OpsError | null;
}

export interface GitWorkspaceSummary {
  workspace_id: string;
  label?: string;
  root?: string;
  is_default?: boolean;
  repository?: boolean;
  source?: string;
}

export interface GitChangedFile {
  path: string;
  original_path?: string;
  index_status?: string;
  worktree_status?: string;
  staged?: boolean;
  unstaged?: boolean;
  untracked?: boolean;
  conflicted?: boolean;
  renamed?: boolean;
  deleted?: boolean;
  binary?: boolean;
  additions?: number;
  deletions?: number;
}

export interface GitCommitSummary {
  sha: string;
  short_sha?: string;
  subject: string;
  author_name?: string;
  author_email?: string;
  authored_at?: string;
  parents?: string[];
  refs?: string[];
}

export interface GitBranchSummary {
  name: string;
  current?: boolean;
  remote?: boolean;
  upstream?: string;
  ahead?: number;
  behind?: number;
  sha?: string;
  last_commit_sha?: string;
  last_commit_subject?: string;
  last_commit_at?: string;
}

export interface GitRemoteSummary {
  name: string;
  fetch_url?: string;
  push_url?: string;
}

export interface GitActivityEntry {
  id?: string;
  timestamp?: string;
  actor?: string;
  operation?: string;
  action?: string;
  outcome?: string;
  summary?: string;
  source?: string;
  task_id?: string | null;
  goal_id?: string | null;
  audit_ref?: string | null;
  trace_id?: string | null;
  approval_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface GitStatus {
  workspace_id: string;
  branch: string;
  head_sha?: string;
  upstream: string;
  remote_name: string;
  ahead?: number;
  behind?: number;
  dirty: boolean;
  detached?: boolean;
  operation_state?: string;
  conflict_count?: number;
  staged_count?: number;
  unstaged_count?: number;
  untracked_count?: number;
  can_commit?: boolean;
  can_pull?: boolean;
  can_push?: boolean;
  changed_files: GitChangedFile[];
  recent_commits: GitCommitSummary[];
  branches?: GitBranchSummary[];
  remotes?: GitRemoteSummary[];
  error?: OpsError | null;
}

export interface GitDiff {
  workspace_id: string;
  cached: boolean;
  path: string;
  diff: string;
  scope?: 'unstaged' | 'staged' | 'combined' | string;
  staged_diff?: string;
  unstaged_diff?: string;
  untracked_diff?: string;
  truncated: boolean;
  additions?: number;
  deletions?: number;
  files_changed?: number;
  error?: OpsError | null;
}

export interface DockerEngineStatus {
  available: boolean;
  boundary: string;
  docker_version: string;
  api_version?: string;
  os?: string;
  architecture?: string;
  compose_available: boolean;
  platform_hint: string;
  error?: OpsError | null;
}

export interface DockerContainerSummary {
  id: string;
  name: string;
  image: string;
  image_id?: string;
  status: string;
  state?: string;
  health?: string;
  ports?: string;
  labels?: Record<string, string>;
  compose_project?: string;
  compose_service?: string;
  created_at?: string;
  command?: string;
  size?: string;
  networks?: Array<string | { name?: string; ip_address?: string; gateway?: string }>;
  mounts?: Array<string | { source?: string; destination?: string; type?: string; read_only?: boolean }>;
  registered?: boolean;
  allowed_actions?: string[];
  uptime?: string;
}

export interface DockerContainerDetails {
  ok?: boolean;
  id?: string;
  name?: string;
  image?: string;
  status?: string;
  command?: string;
  inspect?: {
    state?: Record<string, unknown>;
    health?: Record<string, unknown>;
    resources?: Record<string, unknown>;
    mounts?: Array<Record<string, unknown>>;
    networks?: Record<string, unknown> | Array<Record<string, unknown>>;
    ports?: Record<string, unknown>;
    restart_policy?: Record<string, unknown> | string;
    [key: string]: unknown;
  };
  restart_policy?: string;
  mounts?: Array<{ source?: string; destination?: string; type?: string; read_only?: boolean }>;
  networks?: Array<{ name?: string; ip_address?: string; gateway?: string }>;
  environment_keys?: string[];
}

export interface DockerContainerStats {
  id?: string;
  name?: string;
  cpu_percent?: number | string;
  memory_usage?: number | string;
  memory_limit?: number | string;
  memory_percent?: number | string;
  network_rx?: number;
  network_tx?: number;
  net_io?: string | Record<string, unknown>;
  network_io?: string | Record<string, unknown>;
  block_io?: string | Record<string, unknown>;
  block_read?: number;
  block_write?: number;
  pids?: number | string;
  error?: OpsError | null;
}

export interface DockerInfo {
  id?: string;
  name?: string;
  server_version?: string;
  operating_system?: string;
  architecture?: string;
  kernel_version?: string;
  storage_driver?: string;
  driver?: string;
  cgroup_driver?: string;
  cpus?: number;
  memory_total?: number;
  memory_bytes?: number;
  containers?: number;
  containers_running?: number;
  containers_paused?: number;
  containers_stopped?: number;
  images?: number;
  error?: OpsError | null;
}

export interface DockerImageSummary {
  id: string;
  repository?: string;
  tag?: string;
  digest?: string;
  created_at?: string;
  size?: number | string;
  containers?: number;
}

export interface DockerNetworkSummary {
  id: string;
  name: string;
  driver?: string;
  scope?: string;
  internal?: boolean | string;
  attachable?: boolean | string;
  ipv6?: boolean | string;
  containers?: number | string;
}

export interface DockerVolumeSummary {
  name: string;
  driver?: string;
  scope?: string;
  mountpoint?: string;
  labels?: Record<string, string>;
  in_use?: boolean;
  links?: string[];
  size?: number | string;
}

export interface DockerDiskUsage {
  images?: { count?: number | string; active?: number | string; size?: number | string; reclaimable?: number | string };
  containers?: { count?: number | string; active?: number | string; size?: number | string; reclaimable?: number | string };
  volumes?: { count?: number | string; active?: number | string; size?: number | string; reclaimable?: number | string };
  build_cache?: { count?: number | string; active?: number | string; size?: number | string; reclaimable?: number | string };
  error?: OpsError | null;
}

export interface ComposeServiceStatus {
  name: string;
  container_id?: string;
  state: string;
  status?: string;
  health?: string;
  exit_code?: string;
  ports?: string;
  image?: string;
}

export interface ComposeProjectSummary {
  project_id: string;
  name: string;
  project_directory: string;
  compose_files: string[];
  profiles: string[];
  available_profiles?: string[];
  marker: string;
  category: string;
  allowed_actions?: string[];
  services: ComposeServiceStatus[];
  error?: OpsError | null;
}

export interface OpsTextResult {
  ok: boolean;
  logs?: string;
  config?: string;
  stderr?: string;
  truncated?: boolean;
  error?: OpsError | null;
}

export interface OpsApprovalRequest {
  request_id: string;
  task_id?: string | null;
  goal_id?: string | null;
  trace_id?: string | null;
  tool_name: string;
  digest_prefix: string;
  target_fingerprint_prefix?: string;
  risk_class?: string;
  k_class?: string | null;
  governance_mode?: string;
  status: 'pending' | 'granted' | 'denied' | 'expired' | 'consumed' | 'superseded' | string;
  scope_summary?: Record<string, unknown>;
  created_at?: number;
  expires_at?: number | null;
  decided_at?: number | null;
  decided_by?: string | null;
  decision_reason?: string | null;
  consumed_at?: number | null;
  has_content_payload?: boolean;
}

export type OpsArea = 'git' | 'docker' | 'compose' | 'approvals';
