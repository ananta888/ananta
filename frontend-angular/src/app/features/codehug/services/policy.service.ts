import { Injectable, inject, signal, computed } from '@angular/core';
import { Observable, throwError } from 'rxjs';
import { catchError, map, tap } from 'rxjs/operators';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import {
  ChPolicyDecisionReadModel,
  ChPolicySnapshotReadModel,
  ChPolicyUpdateRequest,
  ChServiceError,
  ChWriteMode,
  DEFAULT_WRITE_MODE_TIMEOUT_MS,
} from '../models/codehug.models';

/**
 * PolicyService — Liest und aendert CodeHug-relevante Policies.
 *
 * SOLID: SRP — ausschliesslich Policy-CRUD. Greift auf dieselbe Hub-API
 * wie features/context-access-policy zu (kein Component-Reuse, nur API).
 *
 * Sicherheit:
 * - Read-only default (writeMode signal)
 * - Schreibversuche ohne write-armed werden abgelehnt
 * - write-mode-Timeout (default 15min) zaehlt herunter
 */
@Injectable({ providedIn: 'root' })
export class PolicyService {
  private readonly hub = inject(HubApiCoreService);
  private readonly dir = inject(AgentDirectoryService);

  /** Aktueller Write-Modus (default read-only). */
  readonly writeMode = signal<ChWriteMode>('read-only');
  /** Unix-Millisekunden, an dem der aktuelle Write-Modus ablaeuft. */
  readonly writeModeExpiresAt = signal<number | null>(null);
  /** Timeout in ms (konfigurierbar). */
  private writeModeTimeoutMs: number = DEFAULT_WRITE_MODE_TIMEOUT_MS;

  /** Computed: ist der write-mode noch aktiv? */
  readonly writeModeActive = computed(() => {
    if (this.writeMode() === 'read-only') return false;
    const exp = this.writeModeExpiresAt();
    if (exp === null) return false;
    return exp > Date.now();
  });

  /** Aktuell geladene Policy. */
  private currentSnapshot: ChPolicySnapshotReadModel | null = null;

  /** Liefert die URL des konfigurierten Hub. */
  private hubUrl(): string {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      throw new ChServiceError('not_found', 'Kein Hub-Agent im AgentDirectory registriert.');
    }
    return hub.url;
  }

  /**
   * Konfiguriert das write-mode-Timeout. 0 = default.
   */
  setWriteModeTimeout(ms: number): void {
    this.writeModeTimeoutMs = ms > 0 ? ms : DEFAULT_WRITE_MODE_TIMEOUT_MS;
  }

  /**
   * Aktiviert den Write-Modus fuer die konfigurierte Dauer.
   * Idempotent: erneuter Aufruf verlangert den Timeout.
   */
  armWriteMode(durationMs?: number): void {
    const ms = durationMs ?? this.writeModeTimeoutMs;
    this.writeMode.set('write-armed');
    this.writeModeExpiresAt.set(Date.now() + ms);
  }

  /**
   * Deaktiviert den Write-Modus sofort.
   */
  disarmWriteMode(): void {
    this.writeMode.set('read-only');
    this.writeModeExpiresAt.set(null);
  }

  /**
   * Prueft ob der Write-Modus noch aktiv ist (und deaktiviert ihn ggf.).
   * Sollte regelmaessig (z.B. pro Tool-Call) aufgerufen werden.
   */
  ensureWriteModeValid(): boolean {
    if (this.writeMode() === 'read-only') return false;
    const exp = this.writeModeExpiresAt();
    if (exp === null || exp <= Date.now()) {
      this.disarmWriteMode();
      return false;
    }
    return true;
  }

  /** Laedt den aktuellen Policy-Snapshot fuer den User. */
  loadCurrentSnapshot(): Observable<ChPolicySnapshotReadModel> {
    const url = `${this.hubUrl()}/api/codehug/policy/current`;
    return this.hub.get<ChPolicySnapshotReadModel>(url, this.hubUrl()).pipe(
      tap(snap => { this.currentSnapshot = this.normalizeSnapshot(snap); }),
      map(snap => this.normalizeSnapshot(snap)),
      catchError(err => throwError(() => this.toChError(err, 'loadCurrentSnapshot'))),
    );
  }

  /** Liefert die letzte geladene Snapshot (synchrone Variante, kein API-Call). */
  getCachedSnapshot(): ChPolicySnapshotReadModel | null {
    return this.currentSnapshot;
  }

  /**
   * Aktualisiert die CodeHug-relevanten Policy-Anteile.
   * Erfordert aktiven Write-Modus.
   */
  update(request: ChPolicyUpdateRequest): Observable<ChPolicySnapshotReadModel> {
    if (!this.ensureWriteModeValid()) {
      throw new ChServiceError(
        'forbidden',
        'Write-Modus nicht aktiv. Aktiviere zuerst den Write-Modus (armWriteMode()).',
      );
    }
    const url = `${this.hubUrl()}/api/codehug/policy`;
    return this.hub.patch<ChPolicySnapshotReadModel>(url, request, this.hubUrl()).pipe(
      tap(snap => { this.currentSnapshot = this.normalizeSnapshot(snap); }),
      map(snap => this.normalizeSnapshot(snap)),
      catchError(err => throwError(() => this.toChError(err, 'update'))),
    );
  }

  /**
   * Liste aller Policy-Decisions (allow/deny/require_approval).
   */
  listDecisions(limit = 100): Observable<ChPolicyDecisionReadModel[]> {
    const url = `${this.hubUrl()}/api/codehug/policy/decisions?limit=${limit}`;
    return this.hub.get<{ decisions: any[] } | ChPolicyDecisionReadModel[]>(url, this.hubUrl()).pipe(
      map(resp => {
        const arr = Array.isArray(resp) ? resp : (resp.decisions ?? []);
        return arr.map(d => this.normalizeDecision(d));
      }),
      catchError(err => throwError(() => this.toChError(err, 'listDecisions'))),
    );
  }

  /**
   * Prueft explizit ob eine spezifische Aktion laut Policy erlaubt ist.
   */
  checkAction(request: {
    actionType: string;
    targetPath?: string;
    toolName?: string;
    profileId?: string;
  }): Observable<ChPolicyDecisionReadModel> {
    const url = `${this.hubUrl()}/api/codehug/policy/check`;
    return this.hub.post<ChPolicyDecisionReadModel>(url, request, this.hubUrl()).pipe(
      map(d => this.normalizeDecision(d)),
      catchError(err => throwError(() => this.toChError(err, 'checkAction'))),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Normalisierung
  // ─────────────────────────────────────────────────────────────────────────

  private normalizeSnapshot(r: any): ChPolicySnapshotReadModel {
    return {
      id: r.id ?? '',
      policyVersion: r.policy_version ?? r.policyVersion ?? '0',
      riskLevel: r.risk_level ?? r.riskLevel ?? 'low',
      allowedTools: r.allowed_tools ?? r.allowedTools ?? [],
      deniedTools: r.denied_tools ?? r.deniedTools ?? [],
      allowedPaths: r.allowed_paths ?? r.allowedPaths ?? [],
      deniedPaths: r.denied_paths ?? r.deniedPaths ?? [],
      sensitiveFilePatterns: r.sensitive_file_patterns ?? r.sensitiveFilePatterns ?? [],
      cloudAllowed: r.cloud_allowed ?? r.cloudAllowed ?? false,
      runtimeBoundary: r.runtime_boundary ?? r.runtimeBoundary ?? 'unknown',
      requiresHumanApproval: r.requires_human_approval ?? r.requiresHumanApproval ?? false,
      approvalReason: r.approval_reason ?? r.approvalReason ?? null,
      createdAt: r.created_at ?? r.createdAt ?? 0,
    };
  }

  private normalizeDecision(d: any): ChPolicyDecisionReadModel {
    return {
      id: d.id ?? '',
      decision: d.decision ?? 'allow',
      decisionType: d.decision_type ?? d.decisionType ?? '',
      reason: d.reason ?? '',
      matchedRuleIds: d.matched_rule_ids ?? d.matchedRuleIds ?? [],
      createdAt: d.created_at ?? d.createdAt ?? 0,
      actionId: d.action_id ?? d.actionId,
      toolCallId: d.tool_call_id ?? d.toolCallId,
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