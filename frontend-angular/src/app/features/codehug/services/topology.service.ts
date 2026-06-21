import { Injectable, inject } from '@angular/core';
import { Observable, of, throwError } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { HubControlCenterApiClient, CcWorkerReadModel, CcSessionReadModel, CcToolCallReadModel, CcPolicyDecisionReadModel } from '../../control-center/services/hub-control-center-api.client';
import {
  ChTopologyReadModel,
  ChHubInstanceReadModel,
  ChWorkerInstanceReadModel,
  ChTopologyConnection,
  ChRoutingRuleReadModel,
  ChTestLayerReadModel,
  ChAgentStepReadModel,
  ChServiceError,
} from '../models/codehug.models';

/**
 * TopologyService — CH-014: Hub/Worker-Topologie, Routing-Regeln, Test-Layer,
 * Trace-Daten aus dem Control-Center-API-Client.
 *
 * SOLID: SRP — ausschliesslich Topologie/Internals-Reads (und
 * Layer-Konfiguration). Konsumiert den bestehenden HubControlCenterApiClient
 * (API-Sharing mit features/control-center), ohne dessen Komponenten zu
 * duplizieren.
 *
 * Sicherheit: Schreibende Operationen (routing-rule-update, layer-update)
 * werden durch PolicyService.writeModeActive() blockiert.
 */
@Injectable({ providedIn: 'root' })
export class TopologyService {
  private readonly hub = inject(HubApiCoreService);
  private readonly dir = inject(AgentDirectoryService);
  private readonly cc = inject(HubControlCenterApiClient);

  /** Liefert die URL des konfigurierten Hub. */
  private hubUrl(): string {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      throw new ChServiceError('not_found', 'Kein Hub-Agent im AgentDirectory registriert.');
    }
    return hub.url;
  }

  /**
   * Laedt die vollstaendige Topologie inkl. Worker, Routing-Regeln und Layer.
   */
  getTopology(): Observable<ChTopologyReadModel> {
    let baseUrl: string;
    try {
      baseUrl = this.hubUrl();
    } catch (err) {
      return throwError(() => this.toChError(err, 'getTopology'));
    }

    return this.cc.listWorkers(baseUrl).pipe(
      map(workersResp => {
        // Hub wraps responses in { data: { items, count }, status }
        const payload = (workersResp as any)?.data ?? workersResp;
        const rawItems: any[] = payload?.items ?? [];
        const chWorkers = rawItems.map((w: any) => this.normalizeWorker(w));
        const hubs = this.detectHubs();
        const connections = this.inferConnections(hubs, chWorkers);
        return {
          hubs,
          workers: chWorkers,
          connections,
          routingRules: [], // separat via getRoutingRules()
          activeLayers: [], // separat via getTestLayers()
        };
      }),
      catchError(err => throwError(() => this.toChError(err, 'getTopology'))),
    );
  }

  /** Aktualisiert die Topologie (z.B. nach Worker-Re-Registrierung). */
  refreshTopology(): Observable<ChTopologyReadModel> {
    return this.getTopology();
  }

  /** Laedt die effektiven Routing-Regeln. */
  getRoutingRules(): Observable<ChRoutingRuleReadModel[]> {
    const url = `${this.hubUrl()}/api/routing/rules`;
    return this.hub.get<{ rules: any[] } | ChRoutingRuleReadModel[]>(url, this.hubUrl()).pipe(
      map(resp => {
        const arr = Array.isArray(resp) ? resp : (resp.rules ?? []);
        return arr.map(r => this.normalizeRoutingRule(r));
      }),
      catchError(err => throwError(() => this.toChError(err, 'getRoutingRules'))),
    );
  }

  /**
   * Aktualisiert eine Routing-Regel. Erfordert write-armed Policy-Modus.
   */
  updateRoutingRule(rule: ChRoutingRuleReadModel): Observable<ChRoutingRuleReadModel> {
    const url = `${this.hubUrl()}/api/routing/rules/${encodeURIComponent(rule.id)}`;
    return this.hub.patch<ChRoutingRuleReadModel>(url, rule, this.hubUrl()).pipe(
      map(r => this.normalizeRoutingRule(r)),
      catchError(err => throwError(() => this.toChError(err, 'updateRoutingRule'))),
    );
  }

  /** Laedt die aktiven Test-Layer. */
  getTestLayers(): Observable<ChTestLayerReadModel[]> {
    const url = `${this.hubUrl()}/api/test-layers`;
    return this.hub.get<{ layers: any[] } | ChTestLayerReadModel[]>(url, this.hubUrl()).pipe(
      map(resp => {
        const arr = Array.isArray(resp) ? resp : (resp.layers ?? []);
        return arr.map(l => this.normalizeLayer(l));
      }),
      catchError(err => throwError(() => this.toChError(err, 'getTestLayers'))),
    );
  }

  /**
   * Aktualisiert einen Test-Layer (enabled/order/parameters).
   * Erfordert write-armed Policy-Modus.
   */
  updateTestLayer(layer: ChTestLayerReadModel): Observable<ChTestLayerReadModel> {
    const url = `${this.hubUrl()}/api/test-layers/${encodeURIComponent(layer.id)}`;
    return this.hub.patch<ChTestLayerReadModel>(url, layer, this.hubUrl()).pipe(
      map(l => this.normalizeLayer(l)),
      catchError(err => throwError(() => this.toChError(err, 'updateTestLayer'))),
    );
  }

  /**
   * Laedt Tool-Calls einer Session (= Trace-Rohdaten).
   */
  getSessionToolCalls(sessionId: string): Observable<CcToolCallReadModel[]> {
    const url = `${this.hubUrl()}/api/sessions/${encodeURIComponent(sessionId)}/tool-calls`;
    return this.cc.listSessionToolCalls(this.hubUrl(), sessionId).pipe(
      map(resp => resp.items ?? []),
      catchError(err => throwError(() => this.toChError(err, 'getSessionToolCalls'))),
    );
  }

  /**
   * Laedt Policy-Decisions einer Session.
   */
  getSessionPolicyDecisions(sessionId: string): Observable<CcPolicyDecisionReadModel[]> {
    return this.cc.listSessionPolicyDecisions(this.hubUrl(), sessionId).pipe(
      map(resp => resp.items ?? []),
      catchError(err => throwError(() => this.toChError(err, 'getSessionPolicyDecisions'))),
    );
  }

  /**
   * Liefert ein Stream-Token fuer SSE.
   */
  createStreamToken(): Observable<{ token: string; expiresAt: number }> {
    return this.cc.createEventStreamToken(this.hubUrl()).pipe(
      map(resp => ({
        token: resp.stream_token ?? resp.token ?? '',
        expiresAt: resp.expires_at,
      })),
      catchError(err => throwError(() => this.toChError(err, 'createStreamToken'))),
    );
  }

  /**
   * Health-check fuer die Topologie (true wenn Workers antworten).
   */
  healthCheck(): Observable<boolean> {
    return this.cc.listWorkers(this.hubUrl()).pipe(
      map(() => true),
      catchError(() => of(false)),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Normalisierung
  // ─────────────────────────────────────────────────────────────────────────

  private detectHubs(): ChHubInstanceReadModel[] {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) return [];
    return [{
      id: hub.name ?? 'hub-1',
      url: hub.url,
      status: 'online',
      version: 'unknown',
      startedAt: 0,
    }];
  }

  private inferConnections(hubs: ChHubInstanceReadModel[], workers: ChWorkerInstanceReadModel[]): ChTopologyConnection[] {
    if (hubs.length === 0) return [];
    const hub = hubs[0];
    return workers.map(w => ({
      hubId: hub.id,
      workerId: w.id,
      transport: 'http',
      status: w.health === 'healthy' ? 'connected' : 'degraded',
    }));
  }

  private normalizeWorker(w: any): ChWorkerInstanceReadModel {
    const id = String(w.id ?? '');
    const rawHealth = String(w.health ?? 'unknown').toLowerCase();
    // Hub liefert "online"/"offline" – auf interne Typen mappen
    const healthMap: Record<string, ChWorkerInstanceReadModel['health']> = {
      online: 'healthy', healthy: 'healthy',
      offline: 'unhealthy', unhealthy: 'unhealthy',
      degraded: 'degraded',
    };
    const health = healthMap[rawHealth] ?? 'unknown';

    const rawBoundary = String(w.boundary ?? w.runtime ?? 'unknown').toLowerCase();
    const boundaryMap: Record<string, ChWorkerInstanceReadModel['boundary']> = {
      'local-only': 'local-only', local: 'local-only',
      'cloud-allowed': 'cloud-allowed', cloud: 'cloud-allowed',
      remote: 'remote',
    };
    const boundary = boundaryMap[rawBoundary] ?? 'unknown';

    return {
      id,
      hubId: 'hub-1',
      type: w.worker_roles?.join(', ') ?? w.role ?? id,
      cliBackend: this.detectCliBackend(w),
      model: w.model ?? w.preferred_model ?? 'unbekannt',
      llmProvider: this.detectProvider(w),
      capabilities: Array.isArray(w.capabilities) ? w.capabilities : [],
      health,
      boundary,
      registeredAt: w.registered_at ? w.registered_at * 1000 : 0,
      lastHeartbeatAt: w.last_seen ? Math.round(w.last_seen * 1000) : null,
    };
  }

  private detectCliBackend(w: any): ChWorkerInstanceReadModel['cliBackend'] {
    const id = (w.id ?? '').toLowerCase();
    const type = (w.type ?? w.role ?? '').toLowerCase();
    const combined = `${id} ${type}`;
    if (combined.includes('sgpt')) return 'sgpt';
    if (combined.includes('opencode')) return 'opencode';
    if (combined.includes('codex')) return 'codex';
    if (combined.includes('claude')) return 'claude_code';
    if (combined.includes('aider')) return 'aider';
    if (combined.includes('mistral')) return 'mistral';
    if (combined.includes('det') || combined.includes('rule')) return 'deterministic';
    // Ananta-Worker = multi-role — als sgpt mappen (häufigster lokaler Backend)
    if (combined.includes('ananta') || combined.includes('alpha') || combined.includes('beta')) return 'sgpt';
    return 'unknown';
  }

  private detectProvider(w: any): ChWorkerInstanceReadModel['llmProvider'] {
    const id = (w.id ?? '').toLowerCase();
    const prov = (w.provider ?? w.llm_provider ?? '').toLowerCase();
    const combined = `${id} ${prov}`;
    if (combined.includes('ollama')) return 'ollama';
    if (combined.includes('lmstudio') || combined.includes('lm-studio')) return 'lmstudio';
    if (combined.includes('openai')) return 'openai';
    if (combined.includes('anthropic') || combined.includes('claude')) return 'anthropic';
    if (combined.includes('openrouter')) return 'openrouter';
    return 'none';
  }

  private normalizeRoutingRule(r: any): ChRoutingRuleReadModel {
    return {
      id: r.id ?? '',
      description: r.description ?? '',
      match: r.match ?? {},
      selectedBackend: r.selected_backend ?? r.selectedBackend ?? 'unknown',
      selectedModel: r.selected_model ?? r.selectedModel ?? '',
      priority: typeof r.priority === 'number' ? r.priority : 0,
    };
  }

  private normalizeLayer(l: any): ChTestLayerReadModel {
    return {
      id: l.id ?? '',
      name: l.name ?? '',
      order: typeof l.order === 'number' ? l.order : 0,
      enabled: Boolean(l.enabled ?? l.active ?? true),
      parameters: l.parameters ?? {},
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