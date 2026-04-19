import { Injectable, inject } from '@angular/core';
import { catchError, forkJoin, of } from 'rxjs';

import { ControlPlaneFacade } from '../features/control-plane/control-plane.facade';
import { GoalDetail, GoalGovernanceSummary, GoalListEntry } from '../models/dashboard.models';
import { NotificationService } from '../services/notification.service';

export interface GoalReportingState {
  goals: GoalListEntry[];
  selectedGoalId: string;
  goalDetail: GoalDetail | null;
  goalGovernance: GoalGovernanceSummary | null;
  loading: boolean;
}

@Injectable({ providedIn: 'root' })
export class DashboardGoalReportingFacade {
  private hubApi = inject(ControlPlaneFacade);
  private ns = inject(NotificationService);

  readonly state: GoalReportingState = {
    goals: [],
    selectedGoalId: '',
    goalDetail: null,
    goalGovernance: null,
    loading: false,
  };

  refresh(hubUrl: string, goalId?: string): void {
    if (goalId) {
      this.state.selectedGoalId = goalId;
    }
    this.state.loading = true;

    this.hubApi.listGoals(hubUrl).subscribe({
      next: goals => this.loadSelectedGoal(hubUrl, this.normalizeGoals(goals)),
      error: () => {
        this.reset();
        this.ns.error('Goals konnten nicht geladen werden');
      },
    });
  }

  recentGoals(limit = 3): GoalListEntry[] {
    return this.state.goals.slice(0, limit);
  }

  activeGoalCount(): number {
    return this.state.goals.filter(
      goal => !['completed', 'failed', 'cancelled'].includes(String(goal?.status || '').toLowerCase())
    ).length;
  }

  costTasks(): any[] {
    const tasks = Array.isArray(this.state.goalDetail?.tasks) ? this.state.goalDetail.tasks : [];
    return [...tasks]
      .filter((task: any) => Number(task?.cost_summary?.cost_units || 0) > 0)
      .sort((left: any, right: any) => Number(right?.cost_summary?.cost_units || 0) - Number(left?.cost_summary?.cost_units || 0))
      .slice(0, 5);
  }

  private loadSelectedGoal(hubUrl: string, goals: GoalListEntry[]): void {
    this.state.goals = goals;
    const selectedId = this.resolveSelectedGoalId(goals);
    if (!selectedId) {
      this.state.selectedGoalId = '';
      this.state.goalDetail = null;
      this.state.goalGovernance = null;
      this.state.loading = false;
      return;
    }

    this.state.selectedGoalId = selectedId;
    forkJoin({
      detail: this.hubApi.getGoalDetail(hubUrl, selectedId).pipe(
        catchError(() => {
          this.ns.error('Goal-Detail konnte nicht geladen werden');
          return of(null);
        })
      ),
      governance: this.hubApi.getGoalGovernanceSummary(hubUrl, selectedId).pipe(
        catchError(() => {
          this.ns.error('Goal-Governance konnte nicht geladen werden');
          return of(null);
        })
      ),
    }).subscribe(({ detail, governance }) => {
      this.state.goalDetail = detail as GoalDetail | null;
      this.state.goalGovernance = governance as GoalGovernanceSummary | null;
      this.state.loading = false;
    });
  }

  private reset(): void {
    this.state.goals = [];
    this.state.selectedGoalId = '';
    this.state.goalDetail = null;
    this.state.goalGovernance = null;
    this.state.loading = false;
  }

  private normalizeGoals(goals: unknown): GoalListEntry[] {
    return Array.isArray(goals)
      ? [...goals].sort(
          (left: any, right: any) =>
            Number(right?.updated_at || right?.created_at || 0) - Number(left?.updated_at || left?.created_at || 0)
        )
      : [];
  }

  private resolveSelectedGoalId(goals: GoalListEntry[]): string {
    if (!goals.length) return '';
    if (this.state.selectedGoalId && goals.some((goal: any) => goal?.id === this.state.selectedGoalId)) {
      return this.state.selectedGoalId;
    }
    return String(goals[0]?.id || '');
  }
}
