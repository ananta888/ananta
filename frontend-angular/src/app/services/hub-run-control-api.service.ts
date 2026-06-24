import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

// ── Domain types ──────────────────────────────────────────────────────────────

export interface RunCommand {
  command_id: string;
  type: string;
  task_id: string | null;
  goal_id: string | null;
  run_id: string | null;
  requested_by: string;
  requested_at: number;
  effective_at: number | null;
  status: string;  // accepted|rejected_by_policy|pending_safe_point|applied|superseded|failed
  result: Record<string, unknown>;
  idempotency_key: string | null;
}

export interface RunCommandRequest {
  type: string;
  payload?: Record<string, unknown>;
  idempotency_key?: string;
  command_id?: string;
}

export interface ApprovalGateSummary {
  request_id: string;
  tool_name: string;
  risk_class: string;
  k_class: string | null;
  digest_prefix: string;
  target_fingerprint_prefix: string;
  scope_summary: Record<string, unknown>;
  expires_at: number | null;
  created_at: number;
  has_content_payload: boolean;
}

export interface BranchCandidate {
  branch_id: string;
  task_id: string | null;
  goal_id: string | null;
  branch_type: string;  // llm_comparison_variant|planner_variant|...
  label: string;
  description: string;
  status: string;  // proposed|active|selected|paused|rejected|superseded|completed
  created_at: number;
  selected_at: number | null;
  metadata: Record<string, unknown>;
}

export interface OperatorInstructionSummary {
  instruction_id: string;
  task_id: string | null;
  goal_id: string | null;
  mode: string;  // next_iteration_instruction|pause_then_apply|context_note_only
  text: string;
  instruction_class: string;
  actor: string;
  created_at: number;
  status: string;  // active|superseded|applied|resolved
  applied_at: number | null;
}

export interface RunControlState {
  task_id: string | null;
  goal_id: string | null;
  task_status: string | null;
  run_status: string | null;  // running|paused|waiting_for_approval|waiting_for_branch_selection|...
  pending_commands: RunCommand[];
  active_instruction: OperatorInstructionSummary | null;
  pending_approvals: ApprovalGateSummary[];
  branches: BranchCandidate[];
  last_events: RunCommand[];
  computed_at: number;
}

// ── Service ───────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class HubRunControlApiService {
  private core = inject(HubApiCoreService);

  // ── Commands ─────────────────────────────────────────────────────────────────

  sendTaskCommand(
    baseUrl: string,
    taskId: string,
    cmd: RunCommandRequest,
    token?: string,
  ): Observable<{ status: string; command: RunCommand }> {
    return this.core.post(
      `${baseUrl}/api/tasks/${encodeURIComponent(taskId)}/commands`,
      cmd,
      baseUrl,
      token,
    );
  }

  sendRunCommand(
    baseUrl: string,
    runId: string,
    cmd: RunCommandRequest,
    token?: string,
  ): Observable<{ status: string; command: RunCommand }> {
    return this.core.post(
      `${baseUrl}/api/runs/${encodeURIComponent(runId)}/commands`,
      cmd,
      baseUrl,
      token,
    );
  }

  sendGoalCommand(
    baseUrl: string,
    goalId: string,
    cmd: RunCommandRequest,
    token?: string,
  ): Observable<{ status: string; command: RunCommand }> {
    return this.core.post(
      `${baseUrl}/api/goals/${encodeURIComponent(goalId)}/commands`,
      cmd,
      baseUrl,
      token,
    );
  }

  // ── Control-state read model ──────────────────────────────────────────────────

  getTaskControlState(
    baseUrl: string,
    taskId: string,
    goalId?: string,
    token?: string,
  ): Observable<{ status: string; control_state: RunControlState }> {
    const q = goalId ? `?goal_id=${encodeURIComponent(goalId)}` : '';
    return this.core.get(
      `${baseUrl}/api/tasks/${encodeURIComponent(taskId)}/control-state${q}`,
      baseUrl,
      token,
      true,
    );
  }

  getRunControlState(
    baseUrl: string,
    runId: string,
    token?: string,
  ): Observable<{ status: string; control_state: RunControlState }> {
    return this.core.get(
      `${baseUrl}/api/runs/${encodeURIComponent(runId)}/control-state`,
      baseUrl,
      token,
      true,
    );
  }

  getGoalControlState(
    baseUrl: string,
    goalId: string,
    token?: string,
  ): Observable<{ status: string; control_state: RunControlState }> {
    return this.core.get(
      `${baseUrl}/api/goals/${encodeURIComponent(goalId)}/control-state`,
      baseUrl,
      token,
      true,
    );
  }

  getAllActiveControlStates(
    baseUrl: string,
    limit = 50,
    token?: string,
  ): Observable<{ status: string; control_states: RunControlState[]; count: number }> {
    return this.core.get(
      `${baseUrl}/api/runs/active-control-state?limit=${limit}`,
      baseUrl,
      token,
      true,
    );
  }

  listTaskCommands(
    baseUrl: string,
    taskId: string,
    limit = 50,
    token?: string,
  ): Observable<{ status: string; commands: RunCommand[]; count: number }> {
    return this.core.get(
      `${baseUrl}/api/tasks/${encodeURIComponent(taskId)}/commands?limit=${limit}`,
      baseUrl,
      token,
      true,
    );
  }

  // ── Branch management ─────────────────────────────────────────────────────────

  listTaskBranches(
    baseUrl: string,
    taskId: string,
    token?: string,
  ): Observable<{ status: string; branches: BranchCandidate[] }> {
    return this.core.get(
      `${baseUrl}/api/tasks/${encodeURIComponent(taskId)}/branches`,
      baseUrl,
      token,
      true,
    );
  }

  createTaskBranch(
    baseUrl: string,
    taskId: string,
    branch: { label: string; branch_type?: string; description?: string; metadata?: Record<string, unknown> },
    token?: string,
  ): Observable<{ status: string; branch: BranchCandidate }> {
    return this.core.post(
      `${baseUrl}/api/tasks/${encodeURIComponent(taskId)}/branches`,
      branch,
      baseUrl,
      token,
    );
  }

  // ── Convenience shorthands ────────────────────────────────────────────────────

  pauseTask(baseUrl: string, taskId: string, token?: string) {
    return this.sendTaskCommand(baseUrl, taskId, { type: 'pause_run' }, token);
  }

  resumeTask(baseUrl: string, taskId: string, instruction?: string, token?: string) {
    return this.sendTaskCommand(baseUrl, taskId, {
      type: 'resume_run',
      payload: instruction ? { instruction, mode: 'next_iteration_instruction' } : {},
    }, token);
  }

  cancelTask(baseUrl: string, taskId: string, token?: string) {
    return this.sendTaskCommand(baseUrl, taskId, { type: 'cancel_run' }, token);
  }

  retryTask(baseUrl: string, taskId: string, token?: string) {
    return this.sendTaskCommand(baseUrl, taskId, { type: 'retry_run_or_task' }, token);
  }

  injectInstruction(
    baseUrl: string,
    taskId: string,
    text: string,
    mode = 'next_iteration_instruction',
    instructionClass = 'constraint',
    token?: string,
  ) {
    return this.sendTaskCommand(baseUrl, taskId, {
      type: 'inject_instruction',
      payload: { text, mode, instruction_class: instructionClass },
    }, token);
  }

  selectBranch(baseUrl: string, taskId: string, branchId: string, reason?: string, token?: string) {
    return this.sendTaskCommand(baseUrl, taskId, {
      type: 'select_branch',
      payload: { branch_id: branchId, reason: reason ?? '' },
    }, token);
  }

  approveGate(baseUrl: string, taskId: string, approvalId: string, reason?: string, token?: string) {
    return this.sendTaskCommand(baseUrl, taskId, {
      type: 'approve_gate',
      payload: { approval_id: approvalId, reason: reason ?? '' },
    }, token);
  }

  denyGate(baseUrl: string, taskId: string, approvalId: string, reason: string, token?: string) {
    return this.sendTaskCommand(baseUrl, taskId, {
      type: 'deny_gate',
      payload: { approval_id: approvalId, reason },
    }, token);
  }
}
