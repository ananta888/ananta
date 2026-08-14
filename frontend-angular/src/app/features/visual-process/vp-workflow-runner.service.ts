import {
  DestroyRef,
  Injectable,
  InjectionToken,
  WritableSignal,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  BehaviorSubject,
  EMPTY,
  Observable,
  Subject,
  catchError,
  exhaustMap,
  map,
  merge,
  of,
  switchMap,
  take,
  timer,
} from 'rxjs';

import {
  DryRunResult,
  ValidationResult,
  VisualProcessApiService,
  VpGraph,
  WorkflowStatus,
  VpRuntimeOverlay,
  sortValidationIssues,
} from './visual-process-api.service';
import {
  VpDecodedRuntime,
  VpRuntimeOverlayContractError,
  decodeVpWorkflowStatus,
} from './vp-runtime-overlay.mapper';

const POLL_INTERVAL_MS = 3000;
const POLL_MAX_MS = 10 * 60 * 1000;

export interface VpWorkflowRunnerClock {
  now(): number;
}

export const VP_WORKFLOW_RUNNER_CLOCK = new InjectionToken<VpWorkflowRunnerClock>(
  'VP_WORKFLOW_RUNNER_CLOCK',
  { providedIn: 'root', factory: () => ({ now: () => Date.now() }) },
);

interface WorkflowPollScope {
  readonly generation: number;
  readonly workflow_id: string;
  readonly graph_id: string;
  readonly started_at: number;
}

interface WorkflowRuntimeFence {
  readonly generation: number;
  readonly workflow_id: string;
  readonly graph_id: string;
  readonly expected_run_id: string | null;
  readonly minimum_revision: number | null;
}

interface WorkflowPollFence extends WorkflowPollScope, WorkflowRuntimeFence {}

interface WorkflowCommandFence extends WorkflowRuntimeFence {
  readonly expected_run_id: string;
  readonly minimum_revision: number;
}

type WorkflowCommand =
  | Readonly<{ kind: 'cancel' }>
  | Readonly<{ kind: 'gate'; action: 'approve' | 'reject'; step_id: string }>;

interface PendingWorkflowCommand {
  readonly key: string;
  readonly command_id: string;
  readonly command: WorkflowCommand;
  readonly fence: WorkflowCommandFence;
}

interface PendingWorkflowStart {
  readonly graph_key: string;
  readonly command_id: string;
}

type WorkflowPollResult =
  | Readonly<{
      kind: 'runtime';
      fence: WorkflowPollFence;
      raw: WorkflowStatus;
      decoded: VpDecodedRuntime;
    }>
  | Readonly<{ kind: 'no_run' | 'stale_revision' | 'timeout'; fence: WorkflowPollFence }>
  | Readonly<{
      kind: 'access_revoked' | 'invalid_contract' | 'unavailable';
      fence: WorkflowPollFence;
      error_code: string;
    }>;

@Injectable()
export class VpWorkflowRunnerService {
  private readonly api = inject(VisualProcessApiService);
  private readonly clock = inject(VP_WORKFLOW_RUNNER_CLOCK);
  private readonly destroyRef = inject(DestroyRef);
  private readonly pollScopes = new BehaviorSubject<WorkflowPollScope | null>(null);
  private readonly refreshRequests = new Subject<void>();
  private generation = 0;
  private activeRunId: string | null = null;
  private acceptedRevision: number | null = null;
  private decodeErrorCode = '';
  private pendingCommand: PendingWorkflowCommand | null = null;
  private pendingCommandInFlight = false;
  private pendingStart: PendingWorkflowStart | null = null;
  private pendingStartInFlight = false;
  private commandIdSequence = 0;

  readonly validationResult = signal<ValidationResult | null>(null);
  readonly dryRunResult = signal<DryRunResult | null>(null);
  readonly activeWorkflowId = signal<string | null>(null);
  readonly workflowStatus = signal<WorkflowStatus | null>(null);
  readonly runtimeOverlay = signal<VpRuntimeOverlay | null>(null);
  readonly status = signal('');

  constructor() {
    this.pollScopes.pipe(
      switchMap(scope => scope ? this.pollScope(scope) : EMPTY),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(result => this.acceptPollResult(result));
  }

  destroy(): void {
    this.generation += 1;
    this.stopPolling();
    this.clearPendingCommand();
    this.clearPendingStart();
  }

  validate(graph: VpGraph): void {
    this.api.validate(graph).subscribe({
      next: result => {
        this.validationResult.set({ ...result, issues: sortValidationIssues(result.issues) });
        this.status.set(result.valid ? 'Gültig ✓' : `${result.error_count} Fehler`);
      },
      error: () => this.status.set('Validierung fehlgeschlagen'),
    });
  }

  dryRun(graph: VpGraph): void {
    this.status.set('Dry-Run läuft…');
    this.api.dryRun(graph).subscribe({
      next: result => {
        this.dryRunResult.set(result);
        this.validationResult.set({ ...result.validation, issues: sortValidationIssues(result.validation.issues) });
        this.status.set('Dry-Run abgeschlossen');
      },
      error: () => this.status.set('Dry-Run fehlgeschlagen'),
    });
  }

  saveAsBlueprint(graph: VpGraph): void {
    this.api.saveAsBlueprint(graph).subscribe({
      next: result => this.status.set(`Blueprint gespeichert (id: ${result.blueprint_id})`),
      error: error => this.status.set(`Blueprint-Fehler: ${error?.error?.detail ?? 'unbekannt'}`),
    });
  }

  refreshPolicyHints(graph: VpGraph, apply: (perStep: Record<string, string[]>) => void): void {
    this.api.policySummary(graph).subscribe({
      next: result => apply(result.per_step),
      error: () => undefined,
    });
  }

  /** Returns false when the logical start is already in flight and no new request was admitted. */
  start(graph: WritableSignal<VpGraph>): boolean {
    const requestedGraph = graph();
    const pendingStart = this.beginStart(requestedGraph);
    if (!pendingStart) return false;
    const generation = ++this.generation;
    this.stopPolling();
    this.activeWorkflowId.set(null);
    this.activeRunId = null;
    this.acceptedRevision = null;
    this.workflowStatus.set(null);
    this.runtimeOverlay.set(null);
    this.clearPendingCommand();
    this.api.startWorkflowFromGraph(requestedGraph, {
      command_id: pendingStart.command_id,
    }).pipe(take(1)).subscribe({
      next: status => {
        if (generation !== this.generation) return;
        this.pendingStartInFlight = false;
        const decoded = this.decodeStatus(
          status,
          requestedGraph.id,
          requestedGraph.id,
          null,
          true,
        );
        if (!decoded) {
          this.failStatus(this.invalidStatusMessage());
          return;
        }
        this.clearPendingStart(pendingStart.command_id);
        this.activeWorkflowId.set(decoded.overlay.workflow_id);
        this.activeRunId = decoded.overlay.run_id;
        this.acceptedRevision = decoded.revision;
        this.acceptStatus(status, decoded);
        if (decoded.terminal) return;
        this.status.set(`Workflow gestartet (id: ${decoded.overlay.workflow_id})`);
        this.startPolling({
          generation,
          workflow_id: decoded.overlay.workflow_id,
          graph_id: requestedGraph.id,
          started_at: this.clock.now(),
        });
      },
      error: error => {
        if (generation !== this.generation) return;
        this.pendingStartInFlight = false;
        const httpStatus = httpErrorStatus(error);
        const ambiguous = httpStatus === null
          || httpStatus === 0
          || httpStatus === 408
          || httpStatus === 429
          || httpStatus >= 500;
        if (!ambiguous) this.clearPendingStart(pendingStart.command_id);
        this.status.set(`Fehler: ${error?.error?.detail ?? 'Workflow konnte nicht gestartet werden'}`);
      },
    });
    return true;
  }

  cancel(): void {
    const workflowId = this.activeWorkflowId();
    if (!workflowId) return;
    const fence = this.captureCommandFence(workflowId);
    if (!fence) {
      this.status.set('Abbrechen erst nach bestätigtem Workflow-Status möglich');
      return;
    }
    const pending = this.beginCommand({ kind: 'cancel' }, fence);
    if (!pending) return;
    this.api.cancelWorkflow(workflowId, '', pending.command_id).pipe(take(1)).subscribe({
      next: response => this.acceptCommandResponse(response, pending),
      error: error => this.acceptCommandTransportError(pending, error, 'Abbrechen fehlgeschlagen'),
    });
  }

  attach(workflowId: string): void {
    const generation = ++this.generation;
    this.stopPolling();
    this.activeWorkflowId.set(workflowId);
    this.activeRunId = null;
    this.acceptedRevision = null;
    this.workflowStatus.set(null);
    this.runtimeOverlay.set(null);
    this.clearPendingCommand();
    this.clearPendingStart();
    this.startPolling({
      generation,
      workflow_id: workflowId,
      graph_id: workflowId,
      started_at: this.clock.now(),
    });
  }

  detach(): void {
    this.generation += 1;
    this.stopPolling();
    this.activeWorkflowId.set(null);
    this.activeRunId = null;
    this.acceptedRevision = null;
    this.clearPendingCommand();
    this.clearPendingStart();
  }

  /**
   * Retire every runtime projection when the editor replaces its graph
   * definition. Unlike detach(), this deliberately does not preserve a
   * terminal snapshot because it cannot be attributed to the replacement.
   */
  clearRuntimeScope(): void {
    this.generation += 1;
    this.stopPolling();
    this.activeWorkflowId.set(null);
    this.activeRunId = null;
    this.acceptedRevision = null;
    this.workflowStatus.set(null);
    this.runtimeOverlay.set(null);
    this.clearPendingCommand();
    this.clearPendingStart();
  }

  refresh(): void {
    if (!this.pollScopes.value) return;
    this.refreshRequests.next();
  }

  signalGate(action: 'approve' | 'reject', stepId: string | null): void {
    const workflowId = this.activeWorkflowId();
    if (!workflowId || !stepId) return;
    const fence = this.captureCommandFence(workflowId);
    if (!fence) {
      this.status.set('Gate-Signal erst nach bestätigtem Workflow-Status möglich');
      return;
    }
    const command: WorkflowCommand = { kind: 'gate', action, step_id: stepId };
    const pending = this.beginCommand(command, fence);
    if (!pending) return;
    this.api.signalWorkflow(
      workflowId,
      action,
      { step_id: stepId },
      pending.command_id,
    ).pipe(take(1)).subscribe({
      next: response => this.acceptCommandResponse(response, pending),
      error: error => this.acceptCommandTransportError(
        pending,
        error,
        `Gate-Fehler: ${error?.error?.detail ?? 'unbekannt'}`,
      ),
    });
  }

  private startPolling(scope: WorkflowPollScope): void {
    this.pollScopes.next(Object.freeze(scope));
  }

  private applyStatus(status: WorkflowStatus): void {
    const decoded = this.decodeStatus(
      status,
      this.activeWorkflowId(),
      this.activeWorkflowId(),
      this.activeRunId,
      true,
    );
    if (!decoded) {
      this.failStatus(this.invalidStatusMessage());
      return;
    }
    if (this.acceptedRevision !== null && decoded.revision! < this.acceptedRevision) return;
    this.acceptPendingCommandObservation(decoded);
    this.activeRunId = decoded.overlay.run_id;
    this.acceptedRevision = decoded.revision;
    this.acceptStatus(status, decoded);
  }

  private decodeStatus(
    status: WorkflowStatus,
    expectedWorkflowId: string | null,
    expectedGraphId: string | null,
    expectedRunId: string | null,
    requireRevision: boolean,
  ): VpDecodedRuntime | null {
    this.decodeErrorCode = '';
    try {
      const decoded = decodeVpWorkflowStatus(status, {
        workflow_id: expectedWorkflowId ?? undefined,
        graph_id: expectedGraphId ?? undefined,
        run_id: expectedRunId ?? undefined,
        require_revision: requireRevision,
      });
      if (decoded.kind === 'no_run') {
        return null;
      }
      return decoded;
    } catch (error) {
      this.decodeErrorCode = error instanceof VpRuntimeOverlayContractError
        ? error.reasonCode
        : '';
      return null;
    }
  }

  private acceptStatus(status: WorkflowStatus, decoded: VpDecodedRuntime): void {
    this.workflowStatus.set(status);
    this.runtimeOverlay.set(decoded.overlay);
    if (decoded.terminal) {
      this.stopPolling();
      this.status.set(
        decoded.normalized_status === 'succeeded'
          ? 'Workflow abgeschlossen ✓'
          : `Workflow ${decoded.normalized_status}`,
      );
    }
  }

  private captureCommandFence(workflowId: string): WorkflowCommandFence | null {
    if (this.activeRunId === null || this.acceptedRevision === null) return null;
    return Object.freeze({
      generation: this.generation,
      workflow_id: workflowId,
      graph_id: workflowId,
      expected_run_id: this.activeRunId,
      minimum_revision: this.acceptedRevision,
    });
  }

  private acceptCommandResponse(
    response: WorkflowStatus,
    pending: PendingWorkflowCommand,
  ): void {
    const { command, fence } = pending;
    if (!this.isPendingCommand(pending)) return;
    this.pendingCommandInFlight = false;
    if (!this.isCurrent(fence)) return;

    let decoded: VpDecodedRuntime;
    try {
      const candidate = decodeVpWorkflowStatus(response, {
        workflow_id: fence.workflow_id,
        graph_id: fence.graph_id,
        run_id: fence.expected_run_id,
        require_revision: true,
      });
      if (candidate.kind === 'no_run') {
        this.acceptCommandContractError(
          pending,
          this.commandContractError(command, 'vp_runtime_run_not_found'),
        );
        return;
      }
      decoded = candidate;
    } catch (error) {
      const reason = error instanceof VpRuntimeOverlayContractError
        ? error.reasonCode
        : 'vp_runtime_response_invalid';
      this.acceptCommandContractError(pending, this.commandContractError(command, reason));
      return;
    }

    if (!this.isCurrent(fence)) return;
    // Every accepted mutating Hub command advances the authoritative runtime
    // revision. A same-revision payload is only old status evidence and must
    // never be presented as a successful cancel/gate acknowledgement.
    if (fence.minimum_revision !== null && decoded.revision! <= fence.minimum_revision) {
      this.ensurePolling(fence);
      this.status.set(this.pendingCommandMessage(command));
      return;
    }
    if (this.acceptedRevision !== null && decoded.revision! < this.acceptedRevision) return;

    this.clearPendingCommand(pending.command_id);
    this.activeRunId = decoded.overlay.run_id;
    this.acceptedRevision = decoded.revision;
    this.workflowStatus.set(response);
    this.runtimeOverlay.set(decoded.overlay);

    switch (decoded.normalized_status) {
      case 'cancelled':
        this.stopPolling();
        this.status.set('Workflow abgebrochen');
        return;
      case 'succeeded':
        this.stopPolling();
        this.status.set('Workflow abgeschlossen ✓');
        return;
      case 'failed':
        this.stopPolling();
        this.status.set('Workflow fehlgeschlagen');
        return;
      case 'skipped':
        this.stopPolling();
        this.status.set('Workflow übersprungen');
        return;
      default:
        this.ensurePolling(fence);
        this.status.set(
          command.kind === 'cancel'
            ? 'Abbruch angefordert…'
            : command.action === 'approve'
              ? 'Gate genehmigt ✓'
              : 'Gate abgelehnt',
        );
    }
  }

  private acceptCommandContractError(pending: PendingWorkflowCommand, message: string): void {
    if (!this.isPendingCommand(pending)) return;
    this.pendingCommandInFlight = false;
    this.ensurePolling(pending.fence);
    this.status.set(message);
  }

  private acceptCommandTransportError(
    pending: PendingWorkflowCommand,
    error: unknown,
    message: string,
  ): void {
    if (!this.isPendingCommand(pending)) return;
    this.pendingCommandInFlight = false;
    const status = httpErrorStatus(error);
    if (status === 401 || status === 403 || status === 404) {
      this.clearRuntimeScope();
      this.status.set(`Workflow-Zugriff nicht mehr verfügbar: vp_runtime_access_revoked_${status}`);
      return;
    }
    const ambiguous = status === null || status === 0 || status === 408 || status === 429 || status >= 500;
    if (!ambiguous) this.clearPendingCommand(pending.command_id);
    else this.ensurePolling(pending.fence);
    this.status.set(message);
  }

  private beginCommand(
    command: WorkflowCommand,
    fence: WorkflowCommandFence,
  ): PendingWorkflowCommand | null {
    const key = this.commandKey(command, fence);
    if (this.pendingCommand !== null
      && (this.pendingCommand.fence.generation !== fence.generation
        || this.pendingCommand.fence.workflow_id !== fence.workflow_id
        || this.pendingCommand.fence.expected_run_id !== fence.expected_run_id
        || this.acceptedRevision !== this.pendingCommand.fence.minimum_revision)) {
      this.clearPendingCommand();
    }
    if (this.pendingCommand !== null && this.pendingCommand.key !== key) {
      this.status.set('Ein anderer Workflow-Befehl wartet noch auf Bestätigung');
      return null;
    }
    if (this.pendingCommandInFlight) {
      this.status.set('Workflow-Befehl wird bereits verarbeitet…');
      return null;
    }
    if (this.pendingCommand === null) {
      this.pendingCommand = Object.freeze({
        key,
        command_id: this.newCommandId(command.kind),
        command,
        fence,
      });
    }
    this.pendingCommandInFlight = true;
    return this.pendingCommand;
  }

  private commandKey(command: WorkflowCommand, fence: WorkflowCommandFence): string {
    return command.kind === 'cancel'
      ? `${fence.workflow_id}\u0000${fence.expected_run_id}\u0000cancel`
      : `${fence.workflow_id}\u0000${fence.expected_run_id}\u0000gate\u0000${command.action}\u0000${command.step_id}`;
  }

  private newCommandId(kind: WorkflowCommand['kind'] | 'start'): string {
    const random = globalThis.crypto?.randomUUID?.();
    const suffix = random ?? `${Math.trunc(this.clock.now())}-${++this.commandIdSequence}`;
    return `vp-${kind}-${suffix}`;
  }

  private isPendingCommand(pending: PendingWorkflowCommand): boolean {
    return this.pendingCommand?.command_id === pending.command_id
      && this.isCurrent(pending.fence)
      && this.activeRunId === pending.fence.expected_run_id;
  }

  private clearPendingCommand(commandId?: string): void {
    if (commandId !== undefined && this.pendingCommand?.command_id !== commandId) return;
    this.pendingCommand = null;
    this.pendingCommandInFlight = false;
  }

  private beginStart(graph: VpGraph): PendingWorkflowStart | null {
    const graphKey = JSON.stringify(graph);
    if (this.pendingStart !== null && this.pendingStart.graph_key !== graphKey) {
      this.clearPendingStart();
    }
    if (this.pendingStartInFlight) {
      this.status.set('Workflow-Start wird bereits verarbeitet…');
      return null;
    }
    if (this.pendingStart === null) {
      this.pendingStart = Object.freeze({
        graph_key: graphKey,
        command_id: this.newCommandId('start'),
      });
    }
    this.pendingStartInFlight = true;
    return this.pendingStart;
  }

  private clearPendingStart(commandId?: string): void {
    if (commandId !== undefined && this.pendingStart?.command_id !== commandId) return;
    this.pendingStart = null;
    this.pendingStartInFlight = false;
  }

  private pendingCommandMessage(command: WorkflowCommand): string {
    if (command.kind === 'cancel') return 'Abbruch wird bestätigt…';
    return command.action === 'approve'
      ? 'Gate-Genehmigung wird bestätigt…'
      : 'Gate-Ablehnung wird bestätigt…';
  }

  private acceptPendingCommandObservation(decoded: VpDecodedRuntime): void {
    const pending = this.pendingCommand;
    if (pending === null) return;
    if (decoded.overlay.workflow_id !== pending.fence.workflow_id
      || decoded.overlay.run_id !== pending.fence.expected_run_id
      || decoded.revision! <= pending.fence.minimum_revision) return;
    this.clearPendingCommand(pending.command_id);
  }

  private commandContractError(command: WorkflowCommand, reason: string): string {
    return command.kind === 'cancel'
      ? `Ungültige Abbruchantwort: ${reason}`
      : `Ungültige Gate-Antwort: ${reason}`;
  }

  private ensurePolling(fence: WorkflowRuntimeFence): void {
    if (this.pollScopes.value !== null) return;
    this.startPolling({
      generation: fence.generation,
      workflow_id: fence.workflow_id,
      graph_id: fence.graph_id,
      started_at: this.clock.now(),
    });
  }

  private stopPolling(): void {
    this.pollScopes.next(null);
  }

  private pollScope(scope: WorkflowPollScope): Observable<WorkflowPollResult> {
    return merge(
      of(undefined),
      timer(POLL_INTERVAL_MS, POLL_INTERVAL_MS).pipe(map(() => undefined)),
      this.refreshRequests,
    ).pipe(
      exhaustMap(() => {
        const fence: WorkflowPollFence = Object.freeze({
          ...scope,
          expected_run_id: this.activeRunId,
          minimum_revision: this.acceptedRevision,
        });
        if (this.clock.now() - scope.started_at > POLL_MAX_MS) {
          return of(Object.freeze({ kind: 'timeout', fence }) as WorkflowPollResult);
        }
        return this.readPoll(fence);
      }),
    );
  }

  private readPoll(fence: WorkflowPollFence): Observable<WorkflowPollResult> {
    return this.api.getWorkflowStatus(fence.workflow_id).pipe(
      take(1),
      map(raw => {
        const decoded = decodeVpWorkflowStatus(raw, {
          workflow_id: fence.workflow_id,
          graph_id: fence.graph_id,
          run_id: fence.expected_run_id ?? undefined,
          require_revision: true,
        });
        if (decoded.kind === 'no_run') {
          return Object.freeze({ kind: 'no_run', fence }) as WorkflowPollResult;
        }
        if (fence.minimum_revision !== null && decoded.revision! < fence.minimum_revision) {
          return Object.freeze({ kind: 'stale_revision', fence }) as WorkflowPollResult;
        }
        return Object.freeze({ kind: 'runtime', fence, raw, decoded }) as WorkflowPollResult;
      }),
      catchError(error => {
        const status = httpErrorStatus(error);
        const accessRevoked = status === 401
          || status === 403
          || (status === 404 && fence.expected_run_id !== null);
        return of(Object.freeze({
          kind: error instanceof VpRuntimeOverlayContractError
            ? 'invalid_contract'
            : accessRevoked
              ? 'access_revoked'
              : 'unavailable',
          fence,
          error_code: error instanceof VpRuntimeOverlayContractError
            ? error.reasonCode
            : accessRevoked
              ? `vp_runtime_access_revoked_${status}`
              : status === 404
                ? 'vp_runtime_run_not_found'
                : 'vp_runtime_status_unavailable',
        }) as WorkflowPollResult);
      }),
    );
  }

  private acceptPollResult(result: WorkflowPollResult): void {
    if (!this.isCurrent(result.fence)) return;
    if (result.kind !== 'runtime' && this.isSuperseded(result.fence)) return;
    switch (result.kind) {
      case 'runtime':
        if (this.activeRunId !== null && this.activeRunId !== result.decoded.overlay.run_id) {
          this.failStatus('Ungültiger Workflow-Status: vp_runtime_run_scope_mismatch');
          this.stopPolling();
          return;
        }
        if (this.acceptedRevision !== null
          && result.decoded.revision! < this.acceptedRevision) return;
        this.acceptPendingCommandObservation(result.decoded);
        this.activeRunId = result.decoded.overlay.run_id;
        this.acceptedRevision = result.decoded.revision;
        this.acceptStatus(result.raw, result.decoded);
        return;
      case 'stale_revision':
        return;
      case 'timeout':
        this.clearRuntimeScope();
        this.status.set('Polling-Timeout (10 min) — Workflow-Status unbekannt');
        return;
      case 'no_run':
        this.failStatus('Kein Workflow-Run verfügbar');
        this.stopPolling();
        return;
      case 'invalid_contract':
        this.clearRuntimeScope();
        this.status.set(`Ungültiger Workflow-Status: ${result.error_code}`);
        return;
      case 'access_revoked':
        this.clearRuntimeScope();
        this.status.set(`Workflow-Zugriff nicht mehr verfügbar: ${result.error_code}`);
        return;
      case 'unavailable':
        this.status.set(result.error_code === 'vp_runtime_run_not_found'
          ? 'Workflow-Run ist noch nicht verfügbar; Status wird erneut geprüft'
          : 'Status konnte nicht geladen werden');
        return;
    }
  }

  private isCurrent(fence: WorkflowRuntimeFence): boolean {
    return fence.generation === this.generation
      && fence.workflow_id === this.activeWorkflowId()
      && fence.graph_id === this.activeWorkflowId();
  }

  private isSuperseded(fence: WorkflowRuntimeFence): boolean {
    return (fence.expected_run_id !== null && fence.expected_run_id !== this.activeRunId)
      || (fence.minimum_revision !== null
        && this.acceptedRevision !== null
        && fence.minimum_revision < this.acceptedRevision);
  }

  private failStatus(message: string): void {
    this.workflowStatus.set(null);
    this.runtimeOverlay.set(null);
    this.status.set(message);
  }

  private invalidStatusMessage(): string {
    return this.decodeErrorCode
      ? `Ungültiger Workflow-Status: ${this.decodeErrorCode}`
      : 'Ungültiger Workflow-Status';
  }
}

function httpErrorStatus(error: unknown): number | null {
  if (typeof error !== 'object' || error === null || !('status' in error)) return null;
  const status = (error as Readonly<{ status?: unknown }>).status;
  return Number.isSafeInteger(status) ? Number(status) : null;
}
