import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { UiAsyncState } from '../../models/ui-async-state';
import { HubApiService } from '../../services/hub-api.service';
import { HubLiveStateService, TaskLogStreamState } from '../../services/hub-live-state.service';
import { HubTaskCollectionStateService } from '../../services/hub-task-collection-state.service';
import {
  HubRunControlApiService,
  RunCommandRequest,
  RunControlState,
} from '../../services/hub-run-control-api.service';
import { ApprovalsApiService } from '../../services/approvals-api.service';

@Injectable({ providedIn: 'root' })
export class TaskManagementFacade {
  private hubApi = inject(HubApiService);
  private liveState = inject(HubLiveStateService);
  private taskCollection = inject(HubTaskCollectionStateService);
  private runControl = inject(HubRunControlApiService);
  private approvalsApi = inject(ApprovalsApiService);

  connectTaskCollection(hubUrl: string | undefined | null, pollMs?: number): void {
    this.taskCollection.connect(hubUrl, pollMs);
  }

  disconnectTaskCollection(hubUrl?: string | null): void {
    this.taskCollection.disconnect(hubUrl);
  }

  reloadTaskCollection(): void {
    this.taskCollection.reload();
  }

  tasks(): any[] {
    return this.taskCollection.tasks();
  }

  tasksLoading(): boolean {
    return this.taskCollection.loading();
  }

  tasksLastLoadedAt(): number | null {
    return this.taskCollection.lastLoadedAt();
  }

  taskCollectionError(): string | null {
    return this.taskCollection.error();
  }

  taskCollectionSnapshot(): {
    tasks: any[];
    loading: boolean;
    refreshing: boolean;
    empty: boolean;
    lastLoadedAt: number | null;
    error: string | null;
    asyncState: UiAsyncState<any[]>;
    counts: Record<string, number>;
  } {
    return this.taskCollection.snapshot();
  }

  childrenOf(taskId: string): any[] {
    return this.taskCollection.childrenOf(taskId);
  }

  ensureSystemEvents(hubUrl: string | undefined | null): void {
    this.liveState.ensureSystemEvents(hubUrl);
  }

  disconnectSystemEvents(): void {
    this.liveState.disconnectSystemEvents();
  }

  systemStreamConnected(): boolean {
    return this.liveState.systemStreamConnected();
  }

  lastSystemEvent(): any | null {
    return this.liveState.lastSystemEvent();
  }

  liveSnapshot(): { systemStreamConnected: boolean; lastSystemEvent: any | null; activeTaskLogStreams: number } {
    return this.liveState.snapshot();
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
    this.liveState.watchTaskLogs(hubUrl, taskId, options);
  }

  taskLogState(taskId: string): TaskLogStreamState {
    return this.liveState.taskLogState(taskId);
  }

  stopTaskLogs(taskId: string | undefined | null): void {
    this.liveState.stopTaskLogs(taskId);
  }

  shouldRefreshTask(log: any): boolean {
    return this.liveState.shouldRefreshTask(log);
  }

  listTasks(hubUrl: string, token?: string) {
    return this.hubApi.listTasks(hubUrl, token);
  }

  listProviderCatalog(hubUrl: string, token?: string) {
    return this.hubApi.listProviderCatalog(hubUrl, token);
  }

  listProviders(hubUrl: string, token?: string) {
    return this.hubApi.listProviders(hubUrl, token);
  }

  listInstructionProfiles(hubUrl: string, ownerUsername?: string, token?: string) {
    return this.hubApi.listInstructionProfiles(hubUrl, ownerUsername, token);
  }

  listInstructionOverlays(
    hubUrl: string,
    filters?: { owner_username?: string; attachment_kind?: string; attachment_id?: string },
    token?: string,
  ) {
    return this.hubApi.listInstructionOverlays(hubUrl, filters, token);
  }

  setTaskInstructionSelection(hubUrl: string, taskId: string, body: any, token?: string) {
    return this.hubApi.setTaskInstructionSelection(hubUrl, taskId, body, token);
  }

  attachInstructionOverlay(hubUrl: string, overlayId: string, body: any, token?: string) {
    return this.hubApi.attachInstructionOverlay(hubUrl, overlayId, body, token);
  }

  detachInstructionOverlay(hubUrl: string, overlayId: string, token?: string) {
    return this.hubApi.detachInstructionOverlay(hubUrl, overlayId, token);
  }

  getInstructionLayersEffective(
    hubUrl: string,
    params?: {
      owner_username?: string;
      task_id?: string;
      goal_id?: string;
      session_id?: string;
      usage_key?: string;
      base_prompt?: string;
      profile_id?: string;
      overlay_id?: string;
    },
    token?: string,
  ) {
    return this.hubApi.getInstructionLayersEffective(hubUrl, params, token);
  }

  getTask(hubUrl: string, taskId: string, token?: string) {
    return this.hubApi.getTask(hubUrl, taskId, token);
  }

  getTaskSources(hubUrl: string, taskId: string, token?: string) {
    return this.hubApi.getTaskSources(hubUrl, taskId, token);
  }

  getTaskAnswerVerification(hubUrl: string, taskId: string, token?: string) {
    return this.hubApi.getTaskAnswerVerification(hubUrl, taskId, token);
  }

  createTask(hubUrl: string, body: any, token?: string) {
    return this.hubApi.createTask(hubUrl, body, token);
  }

  patchTask(hubUrl: string, taskId: string, patch: any, token?: string) {
    return this.hubApi.patchTask(hubUrl, taskId, patch, token);
  }

  assignTask(hubUrl: string, taskId: string, body: any, token?: string) {
    return this.hubApi.assign(hubUrl, taskId, body, token);
  }

  proposeTask(hubUrl: string, taskId: string, body: any, token?: string) {
    return this.hubApi.propose(hubUrl, taskId, body, token);
  }

  executeTask(hubUrl: string, taskId: string, body: any, token?: string) {
    return this.hubApi.execute(hubUrl, taskId, body, token);
  }

  reviewTaskProposal(hubUrl: string, taskId: string, body: any, token?: string) {
    return this.hubApi.reviewTaskProposal(hubUrl, taskId, body, token);
  }

  taskLogs(hubUrl: string, taskId: string, token?: string) {
    return this.hubApi.taskLogs(hubUrl, taskId, token);
  }

  archiveTask(hubUrl: string, taskId: string, token?: string) {
    return this.hubApi.archiveTask(hubUrl, taskId, token);
  }

  cleanupTasks(hubUrl: string, body: any, token?: string) {
    return this.hubApi.cleanupTasks(hubUrl, body, token);
  }

  listArchivedTasks(hubUrl: string, token?: string, limit = 100, offset = 0) {
    return this.hubApi.listArchivedTasks(hubUrl, token, limit, offset);
  }

  restoreTask(hubUrl: string, taskId: string, token?: string) {
    return this.hubApi.restoreTask(hubUrl, taskId, token);
  }

  deleteArchivedTask(hubUrl: string, taskId: string, token?: string) {
    return this.hubApi.deleteArchivedTask(hubUrl, taskId, token);
  }

  cleanupArchivedTasks(hubUrl: string, body: any, token?: string) {
    return this.hubApi.cleanupArchivedTasks(hubUrl, body, token);
  }

  // ── Run-Control (RC-060) ──────────────────────────────────────────────────────

  taskIntervention(hubUrl: string, taskId: string, cmd: RunCommandRequest, token?: string) {
    return this.runControl.sendTaskCommand(hubUrl, taskId, cmd, token);
  }

  pauseTask(hubUrl: string, taskId: string, token?: string) {
    return this.runControl.pauseTask(hubUrl, taskId, token);
  }

  resumeTask(hubUrl: string, taskId: string, instruction?: string, token?: string) {
    return this.runControl.resumeTask(hubUrl, taskId, instruction, token);
  }

  cancelTask(hubUrl: string, taskId: string, token?: string) {
    return this.runControl.cancelTask(hubUrl, taskId, token);
  }

  retryTask(hubUrl: string, taskId: string, token?: string) {
    return this.runControl.retryTask(hubUrl, taskId, token);
  }

  injectInstruction(
    hubUrl: string,
    taskId: string,
    text: string,
    mode?: string,
    instructionClass?: string,
    token?: string,
  ): Observable<any> {
    return this.runControl.injectInstruction(hubUrl, taskId, text, mode, instructionClass, token);
  }

  selectBranch(hubUrl: string, taskId: string, branchId: string, reason?: string, token?: string): Observable<any> {
    return this.runControl.selectBranch(hubUrl, taskId, branchId, reason, token);
  }

  listTaskApprovals(hubUrl: string, taskId: string, token?: string): Observable<any> {
    return this.approvalsApi.listRequests(hubUrl, 'pending', token);
  }

  decideApproval(
    hubUrl: string,
    taskId: string,
    approvalId: string,
    decision: 'granted' | 'denied',
    reason?: string,
    token?: string,
  ): Observable<any> {
    if (decision === 'granted') {
      return this.runControl.approveGate(hubUrl, taskId, approvalId, reason, token);
    }
    return this.runControl.denyGate(hubUrl, taskId, approvalId, reason ?? '', token);
  }

  getTaskControlState(hubUrl: string, taskId: string, goalId?: string, token?: string): Observable<{ status: string; control_state: RunControlState }> {
    return this.runControl.getTaskControlState(hubUrl, taskId, goalId, token);
  }

  getAllActiveControlStates(hubUrl: string, limit = 50, token?: string): Observable<{ status: string; control_states: RunControlState[]; count: number }> {
    return this.runControl.getAllActiveControlStates(hubUrl, limit, token);
  }

  listTaskBranches(hubUrl: string, taskId: string, token?: string): Observable<any> {
    return this.runControl.listTaskBranches(hubUrl, taskId, token);
  }
}
