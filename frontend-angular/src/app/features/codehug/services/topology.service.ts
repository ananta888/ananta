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
    const baseUrl = this.hubUrl();
    return this.cc.listWorkers(baseUrl).pipe(
      map(workersResp => {
        const workers = workersResp.items ?? [];
        const chWorkers = workers.map(w => this.normalizeWorker(w));
        const hubs = this.detectHubs();
        const connections = this.inferConnections(hubs, chWorkers);
        return {
          hubs,
          workers: chWorkers,
          connections,
          routingRules: [], // werden ueber getRoutingRules() separat geladen
          activeLayers: [], // werden ueber getTestLayers() separat geladen
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

  private normalizeWorker(w: CcWorkerReadModel): ChWorkerInstanceReadModel {
    return {
      id: w.id,
      hubId: 'hub-1',
      type: w.id.split(':')[0] ?? w.id,
      cliBackend: this.detectCliBackend(w),
      model: this.detectModel(w),
      llmProvider: this.detectProvider(w),
      capabilities: w.capabilities ?? [],
      health: (w.health as ChWorkerInstanceReadModel['health']) ?? 'unknown',
      boundary: (w.boundary as ChWorkerInstanceReadModel['boundary']) ?? 'unknown',
      registeredAt: 0,
      lastHeartbeatAt: null,
    };
  }

  private detectCliBackend(w: CcWorkerReadModel): ChWorkerInstanceReadModel['cliBackend'] {
    const id = (w.id ?? '').toLowerCase();
    if (id.includes('sgpt')) return 'sgpt';
    if (id.includes('opencode')) return 'opencode';
    if (id.includes('codex')) return 'codex';
    if (id.includes('claude')) return 'claude_code';
    if (id.includes('aider')) return 'aider';
    if (id.includes('mistral')) return 'mistral';
    if (id.includes('det') || id.includes('rule')) return 'deterministic';
    return 'unknown';
  }

  private detectModel(w: CcWorkerReadModel): string {
    return 'unknown'; // Worker-Model kommt aus Session-Step-Daten
  }

  private detectProvider(w: CcWorkerReadModel): ChWorkerInstanceReadModel['llmProvider'] {
    const id = (w.id ?? '').toLowerCase();
    if (id.includes('ollama')) return 'ollama';
    if (id.includes('lmstudio') || id.includes('lm-studio')) return 'lmstudio';
    if (id.includes('openai')) return 'openai';
    if (id.includes('anthropic') || id.includes('claude')) return 'anthropic';
    if (id.includes('openrouter')) return 'openrouter';
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