import { Injectable, OnDestroy, inject, signal } from '@angular/core';
import { Subscription } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiService } from './hub-api.service';

export interface TaskLogStreamState {
  logs: any[];
  loading: boolean;
  connected: boolean;
  lastEvent: any | null;
}

const DEFAULT_TASK_LOG_STATE: TaskLogStreamState = {
  logs: [],
  loading: false,
  connected: false,
  lastEvent: null,
};

const TASK_REFRESH_EVENT_TYPES = new Set([
  'proposal_result',
  'proposal_review',
  'execution_result',
  'task_assigned',
  'task_delegated',
  'task_completed_with_gates',
]);

@Injectable({ providedIn: 'root' })
export class HubLiveStateService implements OnDestroy {
  private hubApi = inject(HubApiService);
  private dir = inject(AgentDirectoryService);

  readonly systemStreamConnected = signal(false);
  readonly lastSystemEvent = signal<any | null>(null);

  private systemHubUrl?: string;
  private systemSub?: Subscription;
  private taskSubs = new Map<string, Subscription>();
  private taskStates = signal<Record<string, TaskLogStreamState>>({});

  ngOnDestroy(): void {
    this.disconnectSystemEvents();
    this.stopAllTaskLogs();
  }

  ensureSystemEvents(hubUrl: string | undefined | null): void {
    const normalizedHubUrl = String(hubUrl || '').trim();
    if (!normalizedHubUrl) return;
    if (this.systemHubUrl === normalizedHubUrl && this.systemSub) return;

    this.disconnectSystemEvents();
    this.systemHubUrl = normalizedHubUrl;
    this.systemSub = this.hubApi.streamSystemEvents(normalizedHubUrl).subscribe({
      next: (event) => {
        this.systemStreamConnected.set(true);
        this.lastSystemEvent.set(event ?? null);
        if (event?.type === 'token_rotated') {
          this.handleTokenRotated(normalizedHubUrl, event?.data?.new_token);
        }
      },
      error: (error) => {
        this.systemStreamConnected.set(false);
        console.error('System events stream error', error);
      },
    });
  }

  disconnectSystemEvents(): void {
    this.systemSub?.unsubscribe();
    this.systemSub = undefined;
    this.systemHubUrl = undefined;
    this.systemStreamConnected.set(false);
  }

  taskLogState(taskId: string): TaskLogStreamState {
    return this.taskStates()[taskId] || DEFAULT_TASK_LOG_STATE;
  }

  watchTaskLogs(
    hubUrl: string,
    taskId: string,
    options?: {
      reset?: boolean;
      onEvent?: (log: any) => void;
      onError?: (error: unknown) => void;
    },
  ): void {
    const normalizedTaskId = String(taskId || '').trim();
    if (!normalizedTaskId) return;

    this.stopTaskLogs(normalizedTaskId);
    this.updateTaskState(normalizedTaskId, {
      logs: options?.reset ? [] : this.taskLogState(normalizedTaskId).logs,
      loading: true,
      connected: false,
      lastEvent: null,
    });

    const sub = this.hubApi.streamTaskLogs(hubUrl, normalizedTaskId).subscribe({
      next: (log) => {
        const prev = this.taskLogState(normalizedTaskId);
        const nextLogs = this.isDuplicateLog(prev.logs, log) ? prev.logs : [...prev.logs, log];
        this.updateTaskState(normalizedTaskId, {
          logs: nextLogs,
          loading: false,
          connected: true,
          lastEvent: log ?? null,
        });
        options?.onEvent?.(log);
      },
      error: (error) => {
        this.updateTaskState(normalizedTaskId, {
          logs: this.taskLogState(normalizedTaskId).logs,
          loading: false,
          connected: false,
          lastEvent: this.taskLogState(normalizedTaskId).lastEvent,
        });
        options?.onError?.(error);
      },
    });
    this.taskSubs.set(normalizedTaskId, sub);
  }

  stopTaskLogs(taskId: string | undefined | null): void {
    const normalizedTaskId = String(taskId || '').trim();
    if (!normalizedTaskId) return;
    this.taskSubs.get(normalizedTaskId)?.unsubscribe();
    this.taskSubs.delete(normalizedTaskId);
    const current = this.taskLogState(normalizedTaskId);
    this.updateTaskState(normalizedTaskId, {
      logs: current.logs,
      loading: false,
      connected: false,
      lastEvent: current.lastEvent,
    });
  }

  shouldRefreshTask(log: any): boolean {
    const eventType = String(log?.event_type || '').trim().toLowerCase();
    return TASK_REFRESH_EVENT_TYPES.has(eventType);
  }

  private stopAllTaskLogs(): void {
    for (const taskId of this.taskSubs.keys()) {
      this.stopTaskLogs(taskId);
    }
  }

  private updateTaskState(taskId: string, nextState: TaskLogStreamState): void {
    this.taskStates.update((current) => ({
      ...current,
      [taskId]: nextState,
    }));
  }

  private isDuplicateLog(existingLogs: any[], candidate: any): boolean {
    return existingLogs.some((entry) => (
      entry?.timestamp === candidate?.timestamp
      && entry?.command === candidate?.command
      && entry?.event_type === candidate?.event_type
      && entry?.reason === candidate?.reason
    ));
  }

  private handleTokenRotated(hubUrl: string, newToken: string | undefined): void {
    const normalizedToken = String(newToken || '').trim();
    if (!normalizedToken) return;

    const agent = this.dir.list().find((item) => item.url === hubUrl);
    if (!agent) return;
    this.dir.upsert({ ...agent, token: normalizedToken });
  }
}
