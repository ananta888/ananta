import { Injectable } from '@angular/core';

import { GoalListEntry } from '../models/dashboard.models';

export interface StarterProgress {
  done: number;
  total: number;
  label: string;
}

@Injectable({ providedIn: 'root' })
export class DashboardWorkspaceViewModelService {
  nextTaskCount(tasks: Array<{ status?: string }> | unknown): number {
    return Array.isArray(tasks)
      ? tasks.filter(task => !['completed', 'done'].includes(String(task?.status || '').toLowerCase())).length
      : 0;
  }

  starterProgress(params: {
    firstStartCompleted: boolean;
    goals: GoalListEntry[];
    hasQuickGoalResult: boolean;
    nextTaskCount: number;
    createdTaskCount: number;
  }): StarterProgress {
    const checks = [
      params.firstStartCompleted,
      params.goals.length > 0 || params.hasQuickGoalResult,
      params.nextTaskCount > 0 || params.createdTaskCount > 0,
    ];
    const done = checks.filter(Boolean).length;
    const label = done >= checks.length ? 'Erste Nutzung ist vorbereitet.' : 'Naechster Schritt bleibt sichtbar.';
    return { done, total: checks.length, label };
  }
}
