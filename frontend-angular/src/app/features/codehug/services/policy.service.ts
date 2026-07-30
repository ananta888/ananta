import { Injectable, inject, signal, computed } from '@angular/core';
import { Observable, throwError } from 'rxjs';
import { catchError, map, switchMap, tap } from 'rxjs/operators';

import { SourceControlAccessDecision } from '../../../models/source-control-v1-api.model';
import { SourceControlV1ApiClient } from '../../../services/source-control-v1-api.client';
import {
  ChPolicyDecisionReadModel,
  ChPolicySnapshotReadModel,
  ChPolicyUpdateRequest,
  ChServiceError,
  ChWriteMode,
  ChAuditEntry,
  ChToolRiskAssessment,
  DEFAULT_WRITE_MODE_TIMEOUT_MS,
} from '../models/codehug.models';

/**
 * PolicyService — Liest und aendert CodeHug-relevante Policies.
 *
 * SOLID: SRP — ausschliesslich Policy-CRUD. Greift auf dieselbe Hub-API
 * wie features/context-access-policy zu (kein Component-Reuse, nur API).
 *
 * Sicherheit:
 * - Der lokale Write-Mode ist ausschliesslich eine UI-Bedienhilfe.
 * - Der Hub autorisiert jede Mutation unabhaengig vom Browserzustand.
 * - Policy-Ladefehler und unbekannte Entscheidungen bleiben fail-closed.
 */
@Injectable({ providedIn: 'root' })
export class PolicyService {
  private readonly sourceControlApi = inject(SourceControlV1ApiClient);

  /** Lokaler UI-Bearbeitungsmodus; er erteilt keine Backend-Berechtigung. */
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
    return this.sourceControlApi.listContextPolicies({ limit: 2 }).pipe(
      switchMap(page => {
        if (page.next_cursor !== null || page.items.length !== 1) {
          return throwError(
            () => new ChServiceError(
              'validation_error',
              'CodeHug benötigt genau eine serverseitig bestätigte Projekt-Policy.',
            ),
          );
        }
        return this.sourceControlApi.getActiveContextPolicy(
          page.items[0].policy_id,
        );
      }),
      map(({ policy }) => this.canonicalSnapshot(policy)),
      tap(snap => { this.currentSnapshot = snap; }),
      catchError(err => {
        this.currentSnapshot = null;
        return throwError(() => this.toChError(err, 'loadCurrentSnapshot'));
      }),
    );
  }

  /** Liefert die letzte geladene Snapshot (synchrone Variante, kein API-Call). */
  getCachedSnapshot(): ChPolicySnapshotReadModel | null {
    return this.currentSnapshot;
  }

  /**
   * Aktualisiert die CodeHug-relevanten Policy-Anteile.
   * Erfordert fuer die UI einen aktiven Bearbeitungsmodus. Die eigentliche
   * Autorisierung muss der Hub fuer jeden Request erneut durchsetzen.
   */
  update(request: ChPolicyUpdateRequest): Observable<ChPolicySnapshotReadModel> {
    if (!this.ensureWriteModeValid()) {
      throw new ChServiceError(
        'forbidden',
        'Lokaler Bearbeitungsmodus nicht aktiv. Dies ist nur eine UI-Sperre; der Hub autorisiert separat.',
      );
    }
    void request;
    return throwError(
      () => new ChServiceError(
        'validation_error',
        'Die Legacy-Snapshot-Mutation ist deaktiviert. Änderungen müssen als kanonischer Policy-Draft mit vollständigem Dokument erfolgen.',
      ),
    );
  }

  /**
   * Liste aller Policy-Decisions (allow/deny/require_approval).
   */
  listDecisions(limit = 100): Observable<ChPolicyDecisionReadModel[]> {
    const boundedLimit = Math.min(625, Math.max(1, Math.trunc(limit)));
    return this.sourceControlApi.loadAccessMatrix({
      operation: 'chat_context',
      transformation: 'redacted',
      purpose: 'code_navigation',
      source_limit: 25,
      destination_limit: 25,
    }).pipe(
      map(matrix => matrix.items
        .slice(0, boundedLimit)
        .map(decision => this.canonicalDecision(decision))),
      catchError(err => throwError(() => this.toChError(err, 'listDecisions'))),
    );
  }

  /**
   * Prueft explizit ob eine spezifische Aktion laut Policy erlaubt ist.
   */
  checkAction(request: {
    actionType: string;
    sourceRevisionId?: string;
    destinationId?: string;
    transformation?: string;
    purpose?: string;
    targetPath?: string;
    toolName?: string;
    profileId?: string;
  }): Observable<ChPolicyDecisionReadModel> {
    if (
      !request.sourceRevisionId ||
      !request.destinationId ||
      !request.transformation ||
      !request.purpose
    ) {
      return throwError(
        () => new ChServiceError(
          'validation_error',
          'Policy-Preview verlangt servergelieferte SourceRevision- und Destination-IDs sowie Transformation und Zweck.',
        ),
      );
    }
    return this.sourceControlApi.previewAccess({
      source_revision_id: request.sourceRevisionId,
      destination_id: request.destinationId,
      operation: request.actionType,
      transformation: request.transformation,
      purpose: request.purpose,
    }).pipe(
      map(d => this.canonicalDecision(d)),
      tap(d => this.appendAudit({ kind: 'policy-check', action: request.actionType, decision: d.decision, reason: d.reason })),
      catchError(err => throwError(() => this.toChError(err, 'checkAction'))),
    );
  }

  /**
   * Lokale Risiko-Einschaetzung fuer ein Tool (deterministisch).
   * Wird VOR dem Tool-Call ausgefuehrt, um User-Warnung zu generieren
   * oder Auto-Approve zu umgehen.
   */
  assessToolRisk(toolName: string, args?: Record<string, unknown>): ChToolRiskAssessment {
    const sensitiveArgs = ['rm -rf', 'sudo ', 'format ', 'drop table', 'eval(', 'exec('];
    const highRiskTools = ['shell_exec', 'write_file', 'delete_file', 'network_request', 'run_command'];
    const mediumRiskTools = ['read_file', 'list_dir', 'search_symbols', 'search_files', 'http_get'];

    const argStr = args ? JSON.stringify(args) : '';
    const hasSensitive = sensitiveArgs.some(s => argStr.toLowerCase().includes(s.toLowerCase()));

    let level: ChToolRiskAssessment['level'] = 'low';
    const reasons: string[] = [];

    if (highRiskTools.includes(toolName)) {
      level = 'high';
      reasons.push(`Tool ${toolName} kann Schreib- oder Netzwerk-Operationen ausfuehren.`);
    } else if (mediumRiskTools.includes(toolName)) {
      level = 'medium';
      reasons.push(`Tool ${toolName} liest externe Ressourcen.`);
    }

    if (hasSensitive) {
      level = 'critical';
      reasons.push('Argumente enthalten potentiell destruktive Muster.');
    }

    const recommendation: ChToolRiskAssessment['recommendation'] =
      level === 'critical' ? 'deny'
      : level === 'high' ? 'require_approval'
      : level === 'medium' ? 'warn'
      : 'allow';

    return { toolName, level, reasons, recommendation, assessedAt: Date.now() };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Lokale Sitzungsdiagnose. Nicht persistent und kein Audit-Nachweis.
  // ─────────────────────────────────────────────────────────────────────────

  private readonly audit = signal<ChAuditEntry[]>([]);
  /** Nicht-autoritative lokale Bedienhistorie; serverseitiges Audit bleibt massgeblich. */
  readonly auditLog = this.audit.asReadonly();
  private readonly auditLimit = 500;

  appendAudit(entry: Omit<ChAuditEntry, 'id' | 'ts'>): ChAuditEntry {
    const full: ChAuditEntry = {
      id: this.makeId('audit'),
      ts: Date.now(),
      ...entry,
    };
    this.audit.update(list => [full, ...list].slice(0, this.auditLimit));
    return full;
  }

  clearAudit(): void {
    this.audit.set([]);
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Rate-Limit (lokal, Frontend-side; Backend hat eigene Quota)
  // ─────────────────────────────────────────────────────────────────────────

  private readonly rateBuckets = new Map<string, { count: number; resetAt: number }>();
  private readonly rateDefaultLimit = 60; // req/min
  private readonly rateWindowMs = 60_000;

  /**
   * Prueft, ob ein neues Request fuer `key` innerhalb des aktuellen
   * Fensters erlaubt ist. Liefert { allowed, remaining, resetInMs }.
   */
  checkRate(key: string, customLimit?: number): { allowed: boolean; remaining: number; resetInMs: number } {
    const limit = customLimit ?? this.rateDefaultLimit;
    const now = Date.now();
    let bucket = this.rateBuckets.get(key);
    if (!bucket || bucket.resetAt <= now) {
      bucket = { count: 0, resetAt: now + this.rateWindowMs };
      this.rateBuckets.set(key, bucket);
    }
    bucket.count++;
    const allowed = bucket.count <= limit;
    return {
      allowed,
      remaining: Math.max(0, limit - bucket.count),
      resetInMs: bucket.resetAt - now,
    };
  }

  resetRate(key?: string): void {
    if (key) this.rateBuckets.delete(key);
    else this.rateBuckets.clear();
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Internals
  // ─────────────────────────────────────────────────────────────────────────

  private makeId(prefix: string): string {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Normalisierung
  // ─────────────────────────────────────────────────────────────────────────

  private canonicalSnapshot(
    policy: {
      readonly policy_id: string;
      readonly version: number;
      readonly document: Readonly<Record<string, unknown>>;
      readonly created_at: string;
    },
  ): ChPolicySnapshotReadModel {
    const defaults =
      policy.document['defaults']
      && typeof policy.document['defaults'] === 'object'
      && !Array.isArray(policy.document['defaults'])
        ? policy.document['defaults'] as Record<string, unknown>
        : {};
    const strings = (value: unknown): string[] =>
      Array.isArray(value)
        ? value.filter((item): item is string =>
            typeof item === 'string' && item.trim().length > 0,
          )
        : [];
    const createdAt = Date.parse(policy.created_at);
    return {
      id: policy.policy_id,
      policyVersion: String(policy.version),
      riskLevel:
        typeof defaults['risk_level'] === 'string'
          ? defaults['risk_level'] as ChPolicySnapshotReadModel['riskLevel']
          : 'unknown' as ChPolicySnapshotReadModel['riskLevel'],
      allowedTools: strings(defaults['allowed_tools']),
      deniedTools: strings(defaults['denied_tools']),
      allowedPaths: strings(defaults['allowed_paths']),
      deniedPaths: strings(defaults['denied_paths']),
      sensitiveFilePatterns: strings(defaults['sensitive_file_patterns']),
      cloudAllowed: defaults['cloud_allowed'] === true,
      runtimeBoundary:
        typeof defaults['runtime_boundary'] === 'string'
          ? defaults['runtime_boundary'] as ChPolicySnapshotReadModel['runtimeBoundary']
          : 'unavailable' as ChPolicySnapshotReadModel['runtimeBoundary'],
      requiresHumanApproval:
        defaults['approval_required'] === true,
      approvalReason:
        typeof defaults['approval_reason'] === 'string'
          ? defaults['approval_reason']
          : null,
      createdAt: Number.isFinite(createdAt) ? createdAt : 0,
    };
  }

  private canonicalDecision(
    value: SourceControlAccessDecision,
  ): ChPolicyDecisionReadModel {
    const decision: ChPolicyDecisionReadModel['decision'] =
      value.decision === 'allow'
        ? 'allow'
        : value.decision === 'approval_required'
          ? 'require_approval'
          : 'deny';
    return {
      id: '',
      decision,
      decisionType: value.operation,
      reason: value.reason_codes.join(', ')
        || (value.decision === 'unavailable'
          ? 'policy_decision_unavailable'
          : 'no_reason_code'),
      matchedRuleIds: [...value.matched_rule_path],
      createdAt: 0,
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
