import { HttpErrorResponse } from '@angular/common/http';
import {
  DestroyRef,
  Injectable,
  InjectionToken,
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
  filter,
  map,
  merge,
  of,
  switchMap,
  take,
  timer,
} from 'rxjs';

import {
  VisualProcessApiService,
  type VpRuntimeOverlay,
} from '../../visual-process/visual-process-api.service';
import {
  VpRuntimeOverlayContractError,
  decodeVpWorkflowStatus,
} from '../../visual-process/vp-runtime-overlay.mapper';
import { CaseFlowEdgeTraceApiService } from './caseflow-edge-trace-api.service';
import type {
  CaseFlowEdgeTraceReadModel,
  CaseFlowEdgeTraceScope,
} from './caseflow-edge-trace.models';
import { decodeCaseFlowEdgeTraceReadModel } from './caseflow-edge-trace.validator';

export interface CaseFlowEdgeTraceReader {
  read(scope: Readonly<CaseFlowEdgeTraceScope>): Observable<unknown>;
}

export const CASEFLOW_EDGE_TRACE_READER = new InjectionToken<CaseFlowEdgeTraceReader>(
  'CASEFLOW_EDGE_TRACE_READER',
  {
    providedIn: 'root',
    factory: () => inject(CaseFlowEdgeTraceApiService),
  },
);

export interface CaseFlowAgentRuntimeSessionConfig {
  readonly poll_interval_ms: number;
  readonly max_initial_not_found_polls: number;
}

export const CASEFLOW_AGENT_RUNTIME_SESSION_CONFIG =
  new InjectionToken<CaseFlowAgentRuntimeSessionConfig>(
    'CASEFLOW_AGENT_RUNTIME_SESSION_CONFIG',
    {
      providedIn: 'root',
      factory: () => ({
        poll_interval_ms: 3000,
        max_initial_not_found_polls: 20,
      }),
    },
  );

export interface CaseFlowAgentRuntimeSessionScope {
  readonly graph_id: string;
  readonly workflow_id: string;
}

export type CaseFlowAgentRuntimeSessionState =
  | 'detached'
  | 'loading'
  | 'no_run'
  | 'no_run_timeout'
  | 'active'
  | 'terminal'
  | 'access_revoked'
  | 'error';

interface AttachedScope extends CaseFlowAgentRuntimeSessionScope {
  readonly generation: number;
}

interface RequestFence extends AttachedScope {
  readonly expected_run_id: string | null;
  readonly minimum_revision: number | null;
  readonly had_evidence: boolean;
}

type PollResult =
  | Readonly<{ kind: 'no_run'; fence: RequestFence }>
  | Readonly<{ kind: 'stale_revision'; fence: RequestFence }>
  | Readonly<{
      kind: 'runtime';
      fence: RequestFence;
      runtime: VpRuntimeOverlay;
      trace: CaseFlowEdgeTraceReadModel | null;
      revision: number;
      terminal: boolean;
      error_code: string | null;
    }>
  | Readonly<{
      kind: 'access_revoked' | 'invalid_contract' | 'transient_status_error';
      fence: RequestFence;
      error_code: string;
    }>;

interface BoundedPollResult {
  readonly result: PollResult;
  readonly suspend_auto: boolean;
  readonly not_found_count: number;
}

/**
 * Studio-scoped, read-only owner for one authoritative runtime/trace session.
 * The Hub API remains the sole evidence source; this facade only decodes,
 * fences and exposes that evidence to CaseFlow projections.
 */
@Injectable()
export class CaseFlowAgentRuntimeSessionFacade {
  private readonly api = inject(VisualProcessApiService);
  private readonly traceReader = inject(CASEFLOW_EDGE_TRACE_READER);
  private readonly config = validateConfig(inject(CASEFLOW_AGENT_RUNTIME_SESSION_CONFIG));
  private readonly destroyRef = inject(DestroyRef);
  private readonly scopes = new BehaviorSubject<AttachedScope | null>(null);
  private readonly refreshRequests = new Subject<void>();
  private generation = 0;

  readonly graphId = signal<string | null>(null);
  readonly workflowId = signal<string | null>(null);
  readonly runId = signal<string | null>(null);
  readonly revision = signal<number | null>(null);
  readonly runtimeOverlay = signal<VpRuntimeOverlay | null>(null);
  readonly edgeTraceReadModel = signal<CaseFlowEdgeTraceReadModel | null>(null);
  readonly state = signal<CaseFlowAgentRuntimeSessionState>('detached');
  readonly errorCode = signal<string | null>(null);

  constructor() {
    this.scopes.pipe(
      switchMap(scope => scope ? this.pollScope(scope) : EMPTY),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(item => this.accept(item));
  }

  attach(scope: Readonly<CaseFlowAgentRuntimeSessionScope>): void {
    const graphId = sessionIdentity(scope.graph_id, 'caseflow_runtime_graph_id_invalid');
    const workflowId = sessionIdentity(
      scope.workflow_id,
      'caseflow_runtime_workflow_id_invalid',
    );
    const attached = Object.freeze({
      graph_id: graphId,
      workflow_id: workflowId,
      generation: ++this.generation,
    });
    this.clearEvidence();
    this.graphId.set(graphId);
    this.workflowId.set(workflowId);
    this.state.set('loading');
    this.scopes.next(attached);
  }

  detach(): void {
    this.generation += 1;
    this.scopes.next(null);
    this.graphId.set(null);
    this.workflowId.set(null);
    this.clearEvidence();
    this.state.set('detached');
  }

  /** A refresh during an in-flight cycle is deliberately coalesced. */
  refresh(): void {
    if (!this.scopes.value) return;
    if (this.state() === 'no_run_timeout') this.state.set('loading');
    this.refreshRequests.next();
  }

  /** Whether an explicit retry can still target the exact attached scope. */
  canRefresh(): boolean {
    return this.scopes.value !== null
      && ['no_run', 'no_run_timeout', 'active', 'error'].includes(this.state());
  }

  /** Stops the session and clears all evidence after an external revocation signal. */
  revokeAccess(errorCode = 'caseflow_runtime_access_revoked'): void {
    this.generation += 1;
    this.scopes.next(null);
    this.failClosed(sessionErrorCode(errorCode), 'access_revoked');
  }

  private pollScope(scope: AttachedScope): Observable<BoundedPollResult> {
    let initialNotFoundCount = 0;
    let autoSuspended = false;
    return merge(
      of(Object.freeze({ manual: false })),
      timer(this.config.poll_interval_ms, this.config.poll_interval_ms).pipe(
        map(() => Object.freeze({ manual: false })),
      ),
      this.refreshRequests.pipe(map(() => Object.freeze({ manual: true }))),
    ).pipe(
      filter(trigger => trigger.manual || !autoSuspended),
      exhaustMap(trigger => {
        if (trigger.manual && autoSuspended) {
          autoSuspended = false;
          initialNotFoundCount = 0;
        }
        return this.readCycle({
          ...scope,
          expected_run_id: this.runId(),
          minimum_revision: this.revision(),
          had_evidence: this.runtimeOverlay() !== null,
        }).pipe(map(result => ({ result, trigger })));
      }),
      map(({ result }) => {
        if (result.kind === 'no_run' && !result.fence.had_evidence) {
          initialNotFoundCount += 1;
        } else if (result.kind !== 'transient_status_error') {
          initialNotFoundCount = 0;
        }
        const boundedNoRun = result.kind === 'no_run'
          && !result.fence.had_evidence
          && initialNotFoundCount >= this.config.max_initial_not_found_polls;
        const suspendAuto = boundedNoRun
          || result.kind === 'access_revoked'
          || result.kind === 'invalid_contract'
          || isTerminalTraceComplete(result);
        autoSuspended = suspendAuto;
        return Object.freeze({
          result,
          suspend_auto: suspendAuto,
          not_found_count: initialNotFoundCount,
        });
      }),
    );
  }

  private readCycle(fence: RequestFence): Observable<PollResult> {
    return this.api.getWorkflowStatus(fence.workflow_id).pipe(
      take(1),
      map(raw => decodeVpWorkflowStatus(raw, {
        workflow_id: fence.workflow_id,
        graph_id: fence.graph_id,
        run_id: fence.expected_run_id ?? undefined,
        require_revision: true,
      })),
      switchMap(decoded => {
        if (decoded.kind === 'no_run') {
          return of(fence.had_evidence
            ? accessRevoked(fence, 'caseflow_runtime_disappeared')
            : Object.freeze({ kind: 'no_run', fence }) as PollResult);
        }
        if (fence.minimum_revision !== null && decoded.revision! < fence.minimum_revision) {
          return of(Object.freeze({ kind: 'stale_revision', fence }) as PollResult);
        }

        const traceScope = Object.freeze({
          workflow_id: decoded.overlay.workflow_id,
          run_id: decoded.overlay.run_id,
        });
        return this.traceReader.read(traceScope).pipe(
          take(1),
          map(rawTrace => Object.freeze({
            kind: 'runtime',
            fence,
            runtime: decoded.overlay,
            trace: decodeCaseFlowEdgeTraceReadModel(rawTrace, traceScope),
            revision: decoded.revision!,
            terminal: decoded.terminal,
            error_code: null,
          }) as PollResult),
          catchError(error => {
            if (isAccessRevoked(error)) {
              return of(accessRevoked(fence, httpErrorCode(error)));
            }
            return of(Object.freeze({
              kind: 'runtime',
              fence,
              runtime: decoded.overlay,
              trace: null,
              revision: decoded.revision!,
              terminal: decoded.terminal,
              error_code: traceErrorCode(error),
            }) as PollResult);
          }),
        );
      }),
      catchError(error => {
        if (isNotFound(error) && !fence.had_evidence) {
          return of(Object.freeze({ kind: 'no_run', fence }) as PollResult);
        }
        if (isAccessRevoked(error)) {
          return of(accessRevoked(fence, httpErrorCode(error)));
        }
        if (error instanceof VpRuntimeOverlayContractError) {
          return of(Object.freeze({
            kind: 'invalid_contract',
            fence,
            error_code: error.reasonCode,
          }) as PollResult);
        }
        return of(Object.freeze({
          kind: 'transient_status_error',
          fence,
          error_code: 'caseflow_runtime_status_unavailable',
        }) as PollResult);
      }),
    );
  }

  private accept(item: BoundedPollResult): void {
    const { result } = item;
    if (!this.isCurrent(result.fence)) return;

    switch (result.kind) {
      case 'runtime':
        if (this.runId() !== null && this.runId() !== result.runtime.run_id) {
          this.failClosed('caseflow_runtime_run_scope_mismatch', 'error');
          return;
        }
        if (this.revision() !== null && result.revision < this.revision()!) return;
        this.runId.set(result.runtime.run_id);
        this.revision.set(result.revision);
        this.runtimeOverlay.set(result.runtime);
        this.edgeTraceReadModel.set(result.trace);
        this.errorCode.set(result.error_code);
        this.state.set(result.terminal ? 'terminal' : 'active');
        if (result.terminal && result.trace === null && result.error_code) {
          this.errorCode.set(`caseflow_runtime_terminal_${result.error_code}`);
        }
        if (item.suspend_auto) this.stopAcceptedSession(result.fence);
        return;
      case 'no_run':
        this.clearEvidence();
        this.state.set(item.suspend_auto ? 'no_run_timeout' : 'no_run');
        this.errorCode.set(item.suspend_auto ? 'caseflow_runtime_no_run_timeout' : null);
        return;
      case 'stale_revision':
        return;
      case 'access_revoked':
        this.failClosed(result.error_code, 'access_revoked');
        this.stopAcceptedSession(result.fence);
        return;
      case 'invalid_contract':
        this.failClosed(result.error_code, 'error');
        this.stopAcceptedSession(result.fence);
        return;
      case 'transient_status_error':
        this.errorCode.set(result.error_code);
        if (this.runtimeOverlay() === null) this.state.set('error');
        return;
    }
  }

  private isCurrent(fence: AttachedScope): boolean {
    return fence.generation === this.generation
      && fence.graph_id === this.graphId()
      && fence.workflow_id === this.workflowId();
  }

  private stopAcceptedSession(fence: AttachedScope): void {
    if (!this.isCurrent(fence)) return;
    this.generation += 1;
    this.scopes.next(null);
  }

  private failClosed(
    errorCode: string,
    state: Extract<CaseFlowAgentRuntimeSessionState, 'access_revoked' | 'error'>,
  ): void {
    this.clearEvidence();
    this.errorCode.set(errorCode);
    this.state.set(state);
  }

  private clearEvidence(): void {
    this.runId.set(null);
    this.revision.set(null);
    this.runtimeOverlay.set(null);
    this.edgeTraceReadModel.set(null);
    this.errorCode.set(null);
  }
}

/**
 * Terminal polling may only stop on trace evidence bound to the terminal run.
 *
 * A decoded trace proves the payload parsed, not that the Hub finished
 * correlating it. An unverified projection is still incomplete, so stopping on
 * it would freeze the canvas on a partial picture of how the run ended.
 */
function isTerminalTraceComplete(result: PollResult): boolean {
  return result.kind === 'runtime'
    && result.terminal
    && result.trace !== null
    && result.trace.verification_status === 'verified';
}

function accessRevoked(fence: RequestFence, errorCode: string): PollResult {
  return Object.freeze({
    kind: 'access_revoked',
    fence,
    error_code: errorCode,
  });
}

function isNotFound(error: unknown): boolean {
  return httpStatus(error) === 404;
}

function isAccessRevoked(error: unknown): boolean {
  return [401, 403, 404].includes(httpStatus(error));
}

function httpStatus(error: unknown): number {
  if (error instanceof HttpErrorResponse) return error.status;
  if (error !== null && typeof error === 'object' && 'status' in error) {
    const status = (error as { readonly status?: unknown }).status;
    return typeof status === 'number' ? status : 0;
  }
  return 0;
}

function httpErrorCode(error: unknown): string {
  const status = httpStatus(error);
  return status > 0 ? `caseflow_runtime_http_${status}` : 'caseflow_runtime_access_revoked';
}

function traceErrorCode(error: unknown): string {
  if (error !== null && typeof error === 'object' && 'reasonCode' in error) {
    const reason = (error as { readonly reasonCode?: unknown }).reasonCode;
    if (typeof reason === 'string' && reason) return reason;
  }
  return 'caseflow_runtime_trace_unavailable';
}

function sessionIdentity(raw: unknown, reason: string): string {
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > 160) throw new Error(reason);
  if (raw !== raw.trim() || Array.from(raw).some(character => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || code === 127;
  })) throw new Error(reason);
  return raw;
}

function sessionErrorCode(raw: unknown): string {
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > 160) {
    return 'caseflow_runtime_access_revoked';
  }
  if (raw !== raw.trim() || !/^[a-z0-9_:-]+$/u.test(raw)) {
    return 'caseflow_runtime_access_revoked';
  }
  return raw;
}

function validateConfig(
  config: Readonly<CaseFlowAgentRuntimeSessionConfig>,
): CaseFlowAgentRuntimeSessionConfig {
  if (!Number.isFinite(config.poll_interval_ms) || config.poll_interval_ms <= 0) {
    throw new Error('caseflow_runtime_poll_interval_invalid');
  }
  if (!Number.isSafeInteger(config.max_initial_not_found_polls)
    || config.max_initial_not_found_polls <= 0) {
    throw new Error('caseflow_runtime_not_found_limit_invalid');
  }
  return Object.freeze({ ...config });
}
