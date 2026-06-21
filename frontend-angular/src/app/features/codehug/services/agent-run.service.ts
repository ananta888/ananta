import { Injectable, inject } from '@angular/core';
import { Observable, Subject, Subscription, throwError } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import {
  ChAgentRunReadModel,
  ChApplyDiffRequest,
  ChApplyDiffResponse,
  ChDiffPreviewResponse,
  ChStartAgentRunRequest,
  ChServiceError,
} from '../models/codehug.models';

/**
 * AgentRunService — kapselt Start/Status/Apply/Abbruch eines Agent-Run.
 *
 * SOLID: SRP — ausschliesslich Agent-Run-Lifecycle. Kontext-Pakete und
 * Profile liegen in eigenen Services.
 *
 * Sicherheit: writeArmed-Status wird sowohl clientseitig (Service-Eingabe)
 * als auch serverseitig (Policy-Snapshot-ID) durchgesetzt. Der Client
 * uebertraegt writeArmed als booleschen Wert, fuehrt aber KEIN write
 * aus wenn dieser false ist.
 */
@Injectable({ providedIn: 'root' })
export class AgentRunService {
  private readonly hub = inject(HubApiCoreService);
  private readonly dir = inject(AgentDirectoryService);

  private readonly liveStreams = new Map<string, Subscription>();

  private hubUrl(): string {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      throw new ChServiceError('not_found', 'Kein Hub-Agent im AgentDirectory registriert.');
    }
    return hub.url;
  }

  /**
   * Startet einen neuen Agent-Run.
   */
  startRun(request: ChStartAgentRunRequest): Observable<{ runId: string }> {
    const url = `${this.hubUrl()}/api/agent-runs`;
    const body = {
      project_id: request.projectId,
      profile_id: request.profileId,
      task_description: request.taskDescription,
      context_package_id: request.contextPackageId,
      risk_level: request.riskLevel,
      write_armed: request.writeArmed,
      template_id: request.templateId,
    };
    return this.hub.post<{ run_id: string }>(url, body, this.hubUrl()).pipe(
      map(resp => ({ runId: resp.run_id })),
      catchError(err => throwError(() => this.toChError(err, 'startRun'))),
    );
  }

  /**
   * Liest den aktuellen Status eines Runs.
   */
  getRun(runId: string): Observable<ChAgentRunReadModel> {
    const url = `${this.hubUrl()}/api/agent-runs/${encodeURIComponent(runId)}`;
    return this.hub.get<ChAgentRunReadModel>(url, this.hubUrl()).pipe(
      map(resp => this.normalizeRun(resp)),
      catchError(err => throwError(() => this.toChError(err, 'getRun'))),
    );
  }

  /**
   * Liste der aktuellen/laufenden Agent-Runs.
   */
  listRuns(projectId?: string): Observable<ChAgentRunReadModel[]> {
    const params = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    const url = `${this.hubUrl()}/api/agent-runs${params}`;
    return this.hub.get<{ runs: any[] } | ChAgentRunReadModel[]>(url, this.hubUrl()).pipe(
      map(resp => {
        const arr = Array.isArray(resp) ? resp : (resp.runs ?? []);
        return arr.map(r => this.normalizeRun(r));
      }),
      catchError(err => throwError(() => this.toChError(err, 'listRuns'))),
    );
  }

  /**
   * Bricht einen laufenden Agent-Run ab.
   */
  cancelRun(runId: string): Observable<void> {
    const url = `${this.hubUrl()}/api/agent-runs/${encodeURIComponent(runId)}/cancel`;
    return this.hub.post<void>(url, {}, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'cancelRun'))),
    );
  }

  /**
   * Liefert die Diff-Vorschau fuer einen Run (falls vorhanden).
   */
  getDiffPreview(runId: string): Observable<ChDiffPreviewResponse> {
    const url = `${this.hubUrl()}/api/agent-runs/${encodeURIComponent(runId)}/diff`;
    return this.hub.get<ChDiffPreviewResponse>(url, this.hubUrl()).pipe(
      catchError(err => throwError(() => this.toChError(err, 'getDiffPreview'))),
    );
  }

  /**
   * Wendet einen freigegebenen Diff ueber die Hub-API an.
   *
   * Sicherheit: applyConfirmationToken wird vom Server erwartet und
   * garantiert, dass der Run tatsaechlich explizit freigegeben wurde.
   */
  applyDiff(request: ChApplyDiffRequest): Observable<ChApplyDiffResponse> {
    if (!request.applyConfirmationToken) {
      throw new ChServiceError('validation_error', 'applyConfirmationToken ist erforderlich.');
    }
    const url = `${this.hubUrl()}/api/agent-runs/${encodeURIComponent(request.runId)}/apply`;
    return this.hub.post<ChApplyDiffResponse>(
      url,
      {
        accepted_file_paths: request.acceptedFilePaths,
        apply_confirmation_token: request.applyConfirmationToken,
      },
      this.hubUrl(),
    ).pipe(
      catchError(err => throwError(() => this.toChError(err, 'applyDiff'))),
    );
  }

  /**
   * Oeffnet einen Live-Stream (Server-Sent Events) fuer einen Run.
   *
   * Konsumenten erhalten fortlaufend aktualisierte ChAgentRunReadModels.
   * Aufraeumung erfolgt durch Aufruf von unsubscribe() ODER closeStream().
   */
  openLiveStream(runId: string): Observable<ChAgentRunReadModel> {
    const subject = new Subject<ChAgentRunReadModel>();
    const streamUrl = `${this.hubUrl()}/api/agent-runs/${encodeURIComponent(runId)}/events`;
    const eventSource = new EventSource(streamUrl);

    const onMessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data);
        subject.next(this.normalizeRun(data));
      } catch (e) {
        // ignore malformed payloads
      }
    };
    const onError = (err: Event) => {
      if (eventSource.readyState === EventSource.CLOSED) {
        subject.complete();
      } else {
        subject.error(err);
      }
    };

    eventSource.addEventListener('message', onMessage as EventListener);
    eventSource.addEventListener('error', onError as EventListener);

    const sub = subject.subscribe({
      complete: () => eventSource.close(),
      error: () => eventSource.close(),
    });
    this.liveStreams.set(runId, sub);

    return new Observable<ChAgentRunReadModel>(observer => {
      const s = subject.subscribe({
        next: v => observer.next(v),
        error: e => observer.error(e),
        complete: () => observer.complete(),
      });
      return () => {
        s.unsubscribe();
        this.closeStream(runId);
      };
    });
  }

  /** Schliesst einen offenen Live-Stream. */
  closeStream(runId: string): void {
    const sub = this.liveStreams.get(runId);
    if (sub) {
      sub.unsubscribe();
      this.liveStreams.delete(runId);
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Normalisierung
  // ─────────────────────────────────────────────────────────────────────────

  private normalizeRun(raw: any): ChAgentRunReadModel {
    return {
      id: raw.id ?? raw.run_id ?? '',
      status: raw.status ?? 'pending',
      projectId: raw.project_id ?? raw.projectId ?? '',
      profileId: raw.profile_id ?? raw.profileId ?? '',
      startedAt: raw.started_at ?? raw.startedAt ?? 0,
      finishedAt: raw.finished_at ?? raw.finishedAt ?? null,
      durationMs: raw.duration_ms ?? raw.durationMs ?? null,
      writeArmed: raw.write_armed ?? raw.writeArmed ?? false,
      steps: (raw.steps ?? []).map((s: any) => this.normalizeStep(s)),
      actualCliBackend: raw.actual_cli_backend ?? raw.actualCliBackend ?? 'unknown',
      actualModel: raw.actual_model ?? raw.actualModel ?? 'unknown',
      actualProvider: raw.actual_provider ?? raw.actualProvider ?? 'none',
      deterministicStepCount: raw.deterministic_step_count ?? raw.deterministicStepCount ?? 0,
      llmStepCount: raw.llm_step_count ?? raw.llmStepCount ?? 0,
      routingReason: raw.routing_reason ?? raw.routingReason ?? '',
      policySnapshotId: raw.policy_snapshot_id ?? raw.policySnapshotId ?? null,
      warnings: raw.warnings ?? [],
    };
  }

  private normalizeStep(s: any): any {
    return {
      id: s.id ?? '',
      index: s.index ?? 0,
      phase: s.phase ?? 'det',
      title: s.title ?? '',
      startedAt: s.started_at ?? s.startedAt ?? 0,
      finishedAt: s.finished_at ?? s.finishedAt ?? null,
      durationMs: s.duration_ms ?? s.durationMs ?? null,
      status: s.status ?? 'pending',
      workerId: s.worker_id ?? s.workerId,
      cliBackend: s.cli_backend ?? s.cliBackend,
      model: s.model,
      toolCalls: (s.tool_calls ?? s.toolCalls ?? []).map((tc: any) => ({
        id: tc.id ?? '',
        toolName: tc.tool_name ?? tc.toolName ?? '',
        riskLevel: tc.risk_level ?? tc.riskLevel ?? 'low',
        targetPath: tc.target_path ?? tc.targetPath ?? null,
        status: tc.status ?? 'pending',
        inputSummary: tc.input_summary ?? tc.inputSummary ?? '',
        outputSummary: tc.output_summary ?? tc.outputSummary,
        startedAt: tc.started_at ?? tc.startedAt ?? 0,
        finishedAt: tc.finished_at ?? tc.finishedAt ?? null,
      })),
      outputSummary: s.output_summary ?? s.outputSummary,
      rawOutput: s.raw_output ?? s.rawOutput,
      stderr: s.stderr,
      args: s.args,
      errorMessage: s.error_message ?? s.errorMessage,
    };
  }

  private toChError(err: unknown, operation: string): ChServiceError {
    let code: any = 'unknown';
    let message = `${operation} failed`;
    if (err instanceof Error) {
      message = `${operation}: ${err.message}`;
      if (err.name === 'TimeoutError') code = 'timeout';
    }
    if (typeof err === 'object' && err !== null) {
      const status = (err as any).status;
      if (status === 401) code = 'unauthorized';
      else if (status === 403) code = 'forbidden';
      else if (status === 404) code = 'not_found';
      else if (status === 422) code = 'validation_error';
      else if (status === 0) code = 'network_error';
      else if (typeof status === 'number' && status >= 500) code = 'backend_error';
    }
    return new ChServiceError(code, message, err);
  }
}