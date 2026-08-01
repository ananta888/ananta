export type ProjectLifecycleStatus = 'active' | 'archived';

export interface ProjectSummary {
  readonly id: string;
  readonly name: string;
  readonly description: string | null;
  readonly status: ProjectLifecycleStatus;
  readonly isActive: boolean;
  readonly origin: 'native' | 'legacy_source_control';
  readonly teamId: string | null;
  readonly version: number;
  readonly createdAt: number;
  readonly updatedAt: number;
  readonly archivedAt: number | null;
}

export interface ProjectCreateRequest {
  readonly name: string;
  readonly description?: string;
}

export interface ProjectContextSnapshot {
  readonly projects: readonly ProjectSummary[];
  readonly selectedProjectId: string;
  readonly loading: boolean;
  readonly ready: boolean;
  readonly error: string;
}
