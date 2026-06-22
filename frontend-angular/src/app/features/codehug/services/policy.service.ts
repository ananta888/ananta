import { Injectable, inject, signal, computed } from '@angular/core';
import { Observable, throwError, of } from 'rxjs';
import { catchError, map, tap } from 'rxjs/operators';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import {
  ChPolicyDecisionReadModel,
  ChPolicySnapshotReadModel,
  ChPolicyUpdateRequest,
  ChServiceError,
  ChWriteMode,
  ChAuditEntry,
  ChToolRiskAssessment,
  DEFAULT_WRITE_MODE_TIMEOUT_MS,
  DEFAULT_SENSITIVE_FILE_PATTERNS,
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
      catchError(() => {
        const def = this._defaultSnapshot();
        this.currentSnapshot = def;
        return of(def);
      }),
    );
  }

  private _defaultSnapshot(): ChPolicySnapshotReadModel {
    return {
      id: 'local-default',
      policyVersion: '1',
      riskLevel: 'low',
      allowedTools: [],
      deniedTools: [],
      allowedPaths: ['/home/krusty/ananta'],
      deniedPaths: [],
      sensitiveFilePatterns: [...DEFAULT_SENSITIVE_FILE_PATTERNS],
      cloudAllowed: false,
      runtimeBoundary: 'local-only',
      requiresHumanApproval: false,
      approvalReason: null,
      createdAt: Date.now(),
    };
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
  // Audit-Log (lokal, in-memory; in Produktion ueber Hub persistiert)
  // ─────────────────────────────────────────────────────────────────────────

  private readonly audit = signal<ChAuditEntry[]>([]);
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