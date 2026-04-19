import { Injectable, inject } from '@angular/core';

import { ControlPlaneFacade } from '../features/control-plane/control-plane.facade';
import { TaskManagementFacade } from '../features/tasks/task-management.facade';
import { NotificationService } from '../services/notification.service';
import { UiAsyncState } from '../models/ui.models';
import {
  AgentEntry,
  ArtifactFlowStatus,
  AutopilotSecurityLevel,
  BenchmarkItem,
  BenchmarkRecommendation,
  BenchmarkTaskKind,
  ContextPolicyStatus,
  ContractsStatus,
  DashboardReadModel,
  DashboardStatsBlock,
  HubCopilotStatus,
  LlmEffectiveRuntime,
  LlmModelReference,
  ResearchBackendStatus,
  RoleEntry,
  RuntimeTelemetry,
  SystemHealth,
  TeamEntry,
  TimelineEvent,
} from '../models/dashboard.models';

export type { BenchmarkTaskKind, AutopilotSecurityLevel } from '../models/dashboard.models';

export interface TimelineFilters {
  teamId?: string;
  agent?: string;
  status?: string;
  errorOnly?: boolean;
}

/**
 * Dashboard-Facade.
 *
 * Kapselt Datenaggregation, Refresh-Semantik, Polling-Koordination und ViewState-Bildung
 * des Dashboards, damit die DashboardComponent kein eigenes HTTP-Orchestration-Zentrum
 * mehr ist.
 *
 * Race-Guard und Safety-Timer leben ebenfalls hier; die Component liest Zustand via
 * Getter und triggert Aktionen über die öffentlichen Methoden.
 */
@Injectable({ providedIn: 'root' })
export class DashboardFacade {
  private hubApi = inject(ControlPlaneFacade);
  private taskFacade = inject(TaskManagementFacade);
  private ns = inject(NotificationService);

  viewState: UiAsyncState = { loading: true, error: null, empty: false };
  stats: DashboardStatsBlock | null = null;
  systemHealth: SystemHealth | null = null;
  contracts: ContractsStatus | null = null;
  history: unknown[] = [];
  agents: AgentEntry[] = [];
  teams: TeamEntry[] = [];
  activeTeam: TeamEntry | null = null;
  roles: RoleEntry[] = [];
  benchmarkData: BenchmarkItem[] = [];
  benchmarkUpdatedAt: number | null = null;
  benchmarkRecommendation: BenchmarkRecommendation | null = null;
  llmDefaults: LlmModelReference | null = null;
  llmExplicitOverride: LlmModelReference | null = null;
  llmEffectiveRuntime: LlmEffectiveRuntime | null = null;
  hubCopilotStatus: HubCopilotStatus | null = null;
  contextPolicyStatus: ContextPolicyStatus | null = null;
  artifactFlowStatus: ArtifactFlowStatus | null = null;
  researchBackendStatus: ResearchBackendStatus | null = null;
  runtimeTelemetry: RuntimeTelemetry | null = null;
  taskTimeline: TimelineEvent[] = [];
  benchmarkTaskKind: BenchmarkTaskKind = 'analysis';

  private readModelInFlight = false;
  private refreshSafetyTimer?: ReturnType<typeof setTimeout>;

  /**
   * Orchestriert den Read-Model-Refresh plus Teams/Roles/Agents und liefert konsistent
   * loading/error/empty. Re-Entry während laufendem Request wird per Guard verhindert.
   */
  refresh(hubUrl: string, benchmarkTaskKind?: BenchmarkTaskKind): void {
    if (this.readModelInFlight) return;
    if (benchmarkTaskKind) this.benchmarkTaskKind = benchmarkTaskKind;

    if (this.refreshSafetyTimer) {
      clearTimeout(this.refreshSafetyTimer);
      this.refreshSafetyTimer = undefined;
    }
    this.viewState = { loading: true, error: null, empty: false };
    this.readModelInFlight = true;
    this.refreshSafetyTimer = setTimeout(() => {
      if (this.viewState.loading) {
        this.viewState = { loading: false, error: 'Dashboard-Daten konnten nicht geladen werden', empty: false };
      }
      this.readModelInFlight = false;
      this.refreshSafetyTimer = undefined;
    }, 15000);

    this.hubApi.getDashboardReadModel(hubUrl, { benchmarkTaskKind: this.benchmarkTaskKind }).subscribe({
      next: (rm: unknown) => this.applyReadModel(rm as DashboardReadModel),
      error: () => this.handleReadModelError(),
    });

    this.hubApi.getStatsHistory(hubUrl).subscribe({
      next: (h: unknown) => this.history = Array.isArray(h) ? h : [],
      error: () => this.ns.error('Dashboard-Historie konnte nicht geladen werden'),
    });

    this.hubApi.listTeams(hubUrl).subscribe({
      next: (teams: unknown) => {
        this.teams = Array.isArray(teams) ? (teams as TeamEntry[]) : [];
        this.activeTeam = this.teams.find(t => t.is_active) ?? null;
      },
      error: () => this.ns.error('Teams konnten nicht geladen werden'),
    });

    this.hubApi.listTeamRoles(hubUrl).subscribe({
      next: (roles: unknown) => this.roles = Array.isArray(roles) ? (roles as RoleEntry[]) : [],
      error: () => this.ns.error('Team-Rollen konnten nicht geladen werden'),
    });

    this.hubApi.listAgents(hubUrl).subscribe({
      next: (agents: unknown) => {
        if (Array.isArray(agents)) {
          this.agents = agents as AgentEntry[];
        } else if (agents && typeof agents === 'object') {
          this.agents = Object.entries(agents as Record<string, Partial<AgentEntry>>).map(
            ([name, info]) => ({
              name: info?.name || name,
              ...info,
            }) as AgentEntry
          );
        } else {
          this.agents = [];
        }
      },
      error: () => this.ns.error('Agentenliste konnte nicht geladen werden'),
    });
  }

  refreshBenchmarks(hubUrl: string, taskKind: BenchmarkTaskKind): void {
    this.hubApi.getLlmBenchmarks(hubUrl, { task_kind: taskKind, top_n: 8 }).subscribe({
      next: (payload: unknown) => {
        const p = (payload as { items?: BenchmarkItem[]; updated_at?: number }) || {};
        this.benchmarkData = Array.isArray(p.items) ? p.items : [];
        this.benchmarkUpdatedAt = Number(p.updated_at || 0) || null;
      },
      error: () => {
        this.benchmarkData = [];
      }
    });
  }

  refreshTaskTimeline(hubUrl: string, filters: TimelineFilters): void {
    this.hubApi.getTaskTimeline(
      hubUrl,
      {
        team_id: filters.teamId || undefined,
        agent: filters.agent || undefined,
        status: filters.status || undefined,
        error_only: filters.errorOnly ?? false,
        limit: 150,
      }
    ).subscribe({
      next: (payload: unknown) => {
        const items = (payload as { items?: TimelineEvent[] } | null)?.items;
        this.taskTimeline = Array.isArray(items) ? items : [];
      },
      error: () => this.ns.error('Task-Timeline konnte nicht geladen werden'),
    });
  }

  /**
   * Räumt Safety-Timer und In-Flight-Guard auf, z.B. bei Component-Destroy.
   */
  dispose(): void {
    if (this.refreshSafetyTimer) {
      clearTimeout(this.refreshSafetyTimer);
      this.refreshSafetyTimer = undefined;
    }
    this.readModelInFlight = false;
  }

  private applyReadModel(rm: DashboardReadModel | null | undefined): void {
    if (this.refreshSafetyTimer) {
      clearTimeout(this.refreshSafetyTimer);
      this.refreshSafetyTimer = undefined;
    }
    this.readModelInFlight = false;

    const sharedTasks = this.taskFacade.tasks();
    const counts = this.buildTaskCounts(sharedTasks);
    const confirmedTaskKind = this.readBenchmarkTaskKindFromReadModel(rm);
    if (confirmedTaskKind) this.benchmarkTaskKind = confirmedTaskKind;
    this.systemHealth = rm?.system_health ?? null;
    this.contracts = rm?.contracts ?? null;
    const agentItems: AgentEntry[] = Array.isArray(rm?.agents?.items) ? rm!.agents!.items! : [];
    this.stats = {
      agents: {
        total: Number(rm?.agents?.count || 0),
        online: agentItems.filter(a => a.status === 'online').length,
        offline: agentItems.filter(a => a.status !== 'online').length,
      },
      tasks: {
        total: Number(counts.total || 0),
        completed: Number(counts.completed || 0),
        failed: Number(counts.failed || 0),
        in_progress: Number(counts.in_progress || 0),
      },
      timestamp: Number(rm?.context_timestamp || Math.floor(Date.now() / 1000)),
      agent_name: String(rm?.system_health?.agent || 'hub'),
    };
    this.teams = Array.isArray(rm?.teams?.items) ? rm!.teams!.items! : [];
    this.roles = Array.isArray(rm?.roles?.items) ? rm!.roles!.items! : [];
    this.agents = agentItems;
    this.benchmarkData = Array.isArray(rm?.benchmarks?.items) ? rm!.benchmarks!.items! : [];
    this.benchmarkUpdatedAt = Number(rm?.benchmarks?.updated_at || 0) || null;
    this.benchmarkRecommendation = rm?.benchmarks?.recommendation ?? null;
    this.llmDefaults = rm?.llm_configuration?.defaults ?? null;
    this.llmExplicitOverride = rm?.llm_configuration?.explicit_override ?? null;
    this.llmEffectiveRuntime = rm?.llm_configuration?.effective_runtime ?? null;
    this.hubCopilotStatus = rm?.llm_configuration?.hub_copilot ?? null;
    this.contextPolicyStatus = rm?.llm_configuration?.context_bundle_policy ?? null;
    this.artifactFlowStatus = rm?.llm_configuration?.artifact_flow ?? null;
    this.researchBackendStatus = rm?.llm_configuration?.research_backend ?? null;
    this.runtimeTelemetry = rm?.llm_configuration?.runtime_telemetry ?? null;
    this.activeTeam = this.teams.find(t => t.is_active) ?? null;
    const recentTasks = (sharedTasks as Array<{ id: string; status?: string; updated_at?: number; created_at?: number }>)
      .slice()
      .sort((left, right) => Number(right?.updated_at || right?.created_at || 0) - Number(left?.updated_at || left?.created_at || 0))
      .slice(0, 30);
    this.taskTimeline = recentTasks.length
      ? recentTasks.map(t => ({
          event_type: 'task_state',
          task_id: t.id,
          task_status: t.status,
          timestamp: t.updated_at || t.created_at || rm?.context_timestamp,
          actor: 'system',
        }))
      : [];
    this.viewState = { loading: false, error: null, empty: !this.stats?.tasks?.total };
  }

  /** Echo des vom Server bestätigten Benchmark-Task-Kinds (Server ist Autorität). */
  readBenchmarkTaskKindFromReadModel(rm: DashboardReadModel | null | undefined): BenchmarkTaskKind | null {
    const responseTaskKind = String(rm?.benchmarks?.task_kind || '').trim();
    if (responseTaskKind === 'analysis' || responseTaskKind === 'coding' || responseTaskKind === 'doc' || responseTaskKind === 'ops') {
      return responseTaskKind;
    }
    return null;
  }

  private handleReadModelError(): void {
    if (this.refreshSafetyTimer) {
      clearTimeout(this.refreshSafetyTimer);
      this.refreshSafetyTimer = undefined;
    }
    this.readModelInFlight = false;
    this.viewState = { loading: false, error: 'Dashboard-Daten konnten nicht geladen werden', empty: false };
    this.ns.error('Dashboard-Daten konnten nicht geladen werden');
  }

  private buildTaskCounts(tasks: Array<{ status?: string }>): Record<string, number> {
    const counts: Record<string, number> = { total: tasks.length, completed: 0, failed: 0, todo: 0, in_progress: 0, blocked: 0 };
    for (const task of tasks) {
      const status = String(task?.status || 'todo').trim().toLowerCase();
      counts[status] = Number(counts[status] || 0) + 1;
    }
    return counts;
  }
}
