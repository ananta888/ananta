import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  ArtifactFlowStatus,
  BenchmarkItem,
  BenchmarkRecommendation,
  BenchmarkTaskKind,
  ContextPolicyStatus,
  HubCopilotStatus,
  LlmEffectiveRuntime,
  LlmModelReference,
  ResearchBackendStatus,
  RuntimeTelemetry,
} from '../models/dashboard.models';

@Component({
  standalone: true,
  selector: 'app-dashboard-benchmark-panel',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card mt-md">
      <div class="row space-between">
        <div>
          <h3 class="no-margin">LLM Benchmark & Empfehlung</h3>
          <div class="muted font-sm mt-sm">
            Vergleich je Aufgabenart mit transparenter Bewertungsgrundlage.
          </div>
        </div>
        <div class="row gap-sm">
          <select aria-label="Benchmark Aufgabenart" [ngModel]="taskKind" (ngModelChange)="taskKindChange.emit($event); refresh.emit()">
            <option value="analysis">analysis</option>
            <option value="coding">coding</option>
            <option value="doc">doc</option>
            <option value="ops">ops</option>
          </select>
          <button class="secondary" (click)="refresh.emit()" aria-label="Benchmark-Daten aktualisieren">Refresh</button>
        </div>
      </div>
      @if (data.length) {
        <div class="grid cols-4 mt-sm">
          <div class="card card-light">
            <div class="muted">Empfohlenes Modell</div>
            <strong>{{ data[0]?.provider }} / {{ data[0]?.model }}</strong>
            <div class="muted status-text-sm-alt">
              Suitability: {{ data[0]?.focus?.suitability_score || 0 | number:'1.0-2' }}%
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Success Rate</div>
            <strong>{{ data[0]?.focus?.success_rate || 0 | percent:'1.0-0' }}</strong>
          </div>
          <div class="card card-light">
            <div class="muted">Quality Rate</div>
            <strong>{{ data[0]?.focus?.quality_rate || 0 | percent:'1.0-0' }}</strong>
          </div>
          <div class="card card-light">
            <div class="muted">Letztes Update</div>
            <strong>{{ updatedAt ? (updatedAt * 1000 | date:'HH:mm:ss') : '-' }}</strong>
          </div>
        </div>
        <div class="grid cols-4 mt-sm">
          <div class="card card-light">
            <div class="muted">Default</div>
            <strong>{{ llmDefaults?.provider || '-' }} / {{ llmDefaults?.model || '-' }}</strong>
            <div class="muted status-text-sm-alt">
              Quelle: {{ llmDefaults?.source?.provider || '-' }}
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Benchmark-Empfehlung</div>
            <strong>{{ recommendation?.recommended?.provider || '-' }} / {{ recommendation?.recommended?.model || '-' }}</strong>
            <div class="muted status-text-sm-alt">
              {{ recommendation?.is_recommendation_active ? 'Aktiv im Runtime-Pfad' : 'Nur Empfehlung, nicht still aktiv' }}
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Expliziter Override</div>
            <strong>{{ llmExplicitOverride?.active ? 'aktiv' : 'kein Override' }}</strong>
            <div class="muted status-text-sm-alt">
              {{ llmExplicitOverride?.provider || '-' }} / {{ llmExplicitOverride?.model || '-' }}
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Hub-Copilot</div>
            <strong>{{ hubCopilotStatus?.active ? 'optional aktiv' : (hubCopilotStatus?.enabled ? 'konfiguriert, aber inaktiv' : 'deaktiviert') }}</strong>
            <div class="muted status-text-sm-alt">
              Mode: {{ hubCopilotStatus?.strategy_mode || '-' }}
            </div>
          </div>
        </div>
        <div class="grid cols-4 mt-sm">
          <div class="card card-light">
            <div class="muted">Context-Policy</div>
            <strong>{{ contextPolicyStatus?.effective?.mode || '-' }}</strong>
            <div class="muted status-text-sm-alt">
              Compact: {{ contextPolicyStatus?.effective?.compact_max_chunks || '-' }} · Standard: {{ contextPolicyStatus?.effective?.standard_max_chunks || '-' }}
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Artifact Flow</div>
            <strong>{{ artifactFlowStatus?.effective?.enabled ? 'enabled' : 'disabled' }}</strong>
            <div class="muted status-text-sm-alt">
              RAG: {{ artifactFlowStatus?.effective?.rag_enabled ? 'on' : 'off' }} · Top-K: {{ artifactFlowStatus?.effective?.rag_top_k || '-' }}
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Effektive Runtime ohne Override</div>
            <strong>{{ llmEffectiveRuntime?.provider || '-' }} / {{ llmEffectiveRuntime?.model || '-' }}</strong>
            <div class="muted status-text-sm-alt">
              {{ llmEffectiveRuntime?.benchmark_applied ? 'Benchmark beeinflusst ungepinnte Requests' : 'Entspricht der konfigurierten Basis' }}
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Runtime-Quelle</div>
            <strong>{{ llmEffectiveRuntime?.selection_source || '-' }}</strong>
            <div class="muted status-text-sm-alt">
              {{ llmEffectiveRuntime?.replaces_configured ? 'Ersetzt die konfigurierte Basis zur Laufzeit' : 'Kein stiller Austausch der konfigurierten Basis' }}
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Research-Backend</div>
            <strong>{{ researchBackendStatus?.provider || '-' }}</strong>
            <div class="muted status-text-sm-alt">
              {{ researchBackendStatus?.enabled ? 'aktiviert' : 'deaktiviert' }} · {{ researchBackendStatus?.configured ? 'konfiguriert' : 'nicht konfiguriert' }}
            </div>
          </div>
          <div class="card card-light">
            <div class="muted">Research-Review</div>
            <strong>{{ researchBackendStatus?.review_policy?.required ? 'required' : 'not required' }}</strong>
            <div class="muted status-text-sm-alt">
              {{ researchBackendStatus?.review_policy?.reason || '-' }}
            </div>
          </div>
        </div>
        @if (activeRuntime()) {
          <div class="grid cols-4 mt-sm">
            <div class="card card-light">
              <div class="muted">Active Inference Runtime</div>
              <strong>{{ activeRuntime()?.provider || '-' }} / {{ activeRuntime()?.model || '-' }}</strong>
              <div class="muted status-text-sm-alt">
                Context: {{ activeRuntime()?.contextLengthLabel || '-' }}
              </div>
              <div class="muted status-text-sm-alt">
                Temperature: {{ activeRuntime()?.temperatureLabel || '-' }}
              </div>
            </div>
            <div class="card card-light">
              <div class="muted">Runtime Executor</div>
              <strong>{{ activeRuntime()?.executorLabel || '-' }}</strong>
              <div class="muted status-text-sm-alt">
                GPU Active: {{ activeRuntime()?.gpuActiveLabel || 'unknown' }}
              </div>
            </div>
            <div class="card card-light">
              <div class="muted">Provider Health</div>
              <strong>{{ activeRuntime()?.providerStatus || '-' }}</strong>
              <div class="muted status-text-sm-alt">
                Reachable: {{ activeRuntime()?.providerReachableLabel || '-' }}
              </div>
            </div>
            <div class="card card-light">
              <div class="muted">Model Inventory</div>
              <strong>{{ activeRuntime()?.candidateCountLabel || '-' }}</strong>
              <div class="muted status-text-sm-alt">
                Source: {{ activeRuntime()?.telemetrySource || '-' }}
              </div>
            </div>
          </div>
          @if (liveModels().length) {
            <div class="table-scroll mt-sm">
              <table class="standard-table table-min-600">
                <thead>
                  <tr class="card-light">
                    <th>Provider</th>
                    <th>Model</th>
                    <th>Executor</th>
                    <th>Context</th>
                    <th>Status</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  @for (row of liveModels(); track row.id) {
                    <tr>
                      <td>{{ row.provider }}</td>
                      <td class="font-mono font-sm">{{ row.model }}</td>
                      <td>{{ row.executorLabel }}</td>
                      <td>{{ row.contextLengthLabel }}</td>
                      <td>{{ row.statusLabel }}</td>
                      <td class="font-mono font-sm">{{ row.sourceLabel }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        }
        @if (researchBackendStatus?.providers) {
          <div class="table-scroll mt-sm">
            <table class="standard-table table-min-600">
              <thead>
                <tr class="card-light">
                  <th>Provider</th>
                  <th>Status</th>
                  <th>Binary</th>
                  <th>Working Dir</th>
                  <th>Mode</th>
                </tr>
              </thead>
              <tbody>
                @for (entry of researchProviders(); track entry.provider) {
                  <tr>
                    <td>{{ entry.provider }}</td>
                    <td>{{ entry.selected ? 'active' : 'optional' }} / {{ entry.configured ? 'configured' : 'missing config' }}</td>
                    <td>{{ entry.binary_available ? 'ok' : 'missing' }}</td>
                    <td>{{ entry.working_dir_exists ? 'ok' : (entry.working_dir ? 'missing' : 'not set') }}</td>
                    <td>{{ entry.mode || '-' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
        <div class="table-scroll mt-sm">
          <table class="standard-table table-min-600">
            <thead>
              <tr class="card-light">
                <th>Rank</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Suitability</th>
                <th>Success</th>
                <th>Quality</th>
                <th>Latency</th>
                <th>Tokens</th>
              </tr>
            </thead>
            <tbody>
              @for (item of data; track item.id; let i = $index) {
                <tr>
                  <td>{{ i + 1 }}</td>
                  <td>{{ item.provider }}</td>
                  <td class="font-mono font-sm">{{ item.model }}</td>
                  <td>{{ item.focus?.suitability_score || 0 | number:'1.0-2' }}%</td>
                  <td>{{ item.focus?.success_rate || 0 | percent:'1.0-0' }}</td>
                  <td>{{ item.focus?.quality_rate || 0 | percent:'1.0-0' }}</td>
                  <td>{{ item.focus?.avg_latency_ms || 0 | number:'1.0-0' }} ms</td>
                  <td>{{ item.focus?.avg_tokens || 0 | number:'1.0-0' }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      } @else {
        <div class="muted mt-sm">Noch keine Benchmarkdaten vorhanden.</div>
      }
    </div>
  `,
})
export class DashboardBenchmarkPanelComponent {
  @Input() data: BenchmarkItem[] = [];
  @Input() updatedAt: number | null = null;
  @Input() recommendation: BenchmarkRecommendation | null = null;
  @Input() llmDefaults: LlmModelReference | null = null;
  @Input() llmExplicitOverride: LlmModelReference | null = null;
  @Input() llmEffectiveRuntime: LlmEffectiveRuntime | null = null;
  @Input() hubCopilotStatus: HubCopilotStatus | null = null;
  @Input() contextPolicyStatus: ContextPolicyStatus | null = null;
  @Input() artifactFlowStatus: ArtifactFlowStatus | null = null;
  @Input() researchBackendStatus: ResearchBackendStatus | null = null;
  @Input() runtimeTelemetry: RuntimeTelemetry | null = null;

  @Input() taskKind: BenchmarkTaskKind = 'analysis';
  @Output() taskKindChange = new EventEmitter<BenchmarkTaskKind>();
  @Output() refresh = new EventEmitter<void>();

  researchProviders(): any[] {
    const providers = this.researchBackendStatus?.providers;
    if (!providers || typeof providers !== 'object') return [];
    return Object.values(providers) as any[];
  }

  activeRuntime(): any | null {
    const runtimeProviders = this.runtimeTelemetry?.providers;
    if (!runtimeProviders || typeof runtimeProviders !== 'object') return null;
    const provider = String(
      this.llmEffectiveRuntime?.provider ||
      this.llmDefaults?.provider ||
      this.llmExplicitOverride?.provider ||
      ''
    ).trim().toLowerCase();
    if (!provider) return null;

    const model = String(
      this.llmEffectiveRuntime?.model ||
      this.llmDefaults?.model ||
      this.llmExplicitOverride?.model ||
      ''
    ).trim();
    const providerState: any = (runtimeProviders as Record<string, any>)?.[provider] || null;
    if (!providerState) return null;

    const contextLength = this.resolveContextLength(provider, model, providerState);
    const ollamaActivity = provider === 'ollama' ? (providerState?.activity || null) : null;
    const executorSummary = ollamaActivity?.executor_summary || {};
    const gpuActive = ollamaActivity?.gpu_active;
    const temperatureRaw = this.llmEffectiveRuntime?.temperature ?? this.hubCopilotStatus?.effective?.temperature ?? null;
    const temperature = Number.isFinite(Number(temperatureRaw)) ? Number(temperatureRaw) : null;
    const activeEntry = provider === 'ollama'
      ? ((Array.isArray(ollamaActivity?.active_models) ? ollamaActivity.active_models : []).find((item: any) => String(item?.name || '').trim() === model) || null)
      : null;
    const executor = String(activeEntry?.executor || '').trim().toLowerCase();
    const executorLabel = executor ? executor.toUpperCase() : (
      Number(executorSummary?.gpu || 0) > 0 ? 'GPU'
      : Number(executorSummary?.cpu || 0) > 0 ? 'CPU'
      : 'unknown'
    );

    return {
      provider,
      model: model || '-',
      contextLengthLabel: contextLength ? `${contextLength} tokens` : 'unknown',
      temperatureLabel: temperature === null ? 'default' : temperature.toFixed(2),
      executorLabel,
      gpuActiveLabel: gpuActive === true ? 'yes' : gpuActive === false ? 'no' : 'unknown',
      providerStatus: String(providerState?.status || 'unknown'),
      providerReachableLabel: providerState?.reachable === true ? 'yes' : providerState?.reachable === false ? 'no' : 'unknown',
      candidateCountLabel: String(Number(providerState?.candidate_count || 0)),
      telemetrySource: provider === 'ollama' ? '/api/tags + /api/ps' : '/v1/models',
    };
  }

  liveModels(): any[] {
    const runtimeProviders = this.runtimeTelemetry?.providers;
    if (!runtimeProviders || typeof runtimeProviders !== 'object') return [];
    const rows: any[] = [];
    const activeProvider = String(this.llmEffectiveRuntime?.provider || '').trim().toLowerCase();
    const activeModel = String(this.llmEffectiveRuntime?.model || '').trim();

    const ollama = runtimeProviders?.ollama;
    if (ollama && typeof ollama === 'object') {
      const ollamaModels = Array.isArray(ollama?.models) ? ollama.models : [];
      const activeModels = Array.isArray(ollama?.activity?.active_models) ? ollama.activity.active_models : [];
      for (const entry of activeModels) {
        const model = String(entry?.name || '').trim();
        if (!model) continue;
        const modelDef = ollamaModels.find((item: any) => String(item?.name || '').trim() === model) || null;
        const contextLength = Number(
          entry?.context_length ||
          entry?.num_ctx ||
          modelDef?.context_length ||
          modelDef?.num_ctx ||
          modelDef?.details?.context_length ||
          modelDef?.details?.num_ctx ||
          0
        );
        const contextLengthLabel = Number.isFinite(contextLength) && contextLength > 0 ? `${contextLength} tokens` : 'unknown';
        const executor = String(entry?.executor || '').trim().toLowerCase();
        rows.push({
          id: `ollama:${model}:${executor || 'unknown'}`,
          provider: 'ollama',
          model,
          executorLabel: executor ? executor.toUpperCase() : 'unknown',
          contextLengthLabel,
          statusLabel: activeProvider === 'ollama' && activeModel === model ? 'active runtime' : 'active',
          sourceLabel: '/api/ps',
        });
      }
    }

    const lmstudio = runtimeProviders?.lmstudio;
    if (lmstudio && typeof lmstudio === 'object') {
      const candidates = Array.isArray(lmstudio?.candidates) ? lmstudio.candidates : [];
      for (const entry of candidates) {
        const model = String(entry?.id || entry?.name || '').trim();
        if (!model) continue;
        const contextLength = Number(entry?.context_length || entry?.num_ctx || 0);
        const contextLengthLabel = Number.isFinite(contextLength) && contextLength > 0 ? `${contextLength} tokens` : 'unknown';
        const isActiveRuntime = activeProvider === 'lmstudio' && activeModel === model;
        const loaded = entry?.loaded === true;
        rows.push({
          id: `lmstudio:${model}`,
          provider: 'lmstudio',
          model,
          executorLabel: loaded ? 'loaded' : 'unknown',
          contextLengthLabel,
          statusLabel: isActiveRuntime ? 'active runtime' : (loaded ? 'loaded' : 'available'),
          sourceLabel: '/v1/models',
        });
      }
    }

    rows.sort((left: any, right: any) => {
      const leftActive = String(left?.statusLabel || '').includes('active') ? 1 : 0;
      const rightActive = String(right?.statusLabel || '').includes('active') ? 1 : 0;
      if (leftActive !== rightActive) return rightActive - leftActive;
      return String(left?.model || '').localeCompare(String(right?.model || ''));
    });
    return rows;
  }

  private resolveContextLength(provider: string, model: string, providerState: any): number | null {
    if (!model) return null;
    if (provider === 'lmstudio') {
      const candidates = Array.isArray(providerState?.candidates) ? providerState.candidates : [];
      const candidate = candidates.find((item: any) => String(item?.id || '').trim() === model);
      const value = Number(candidate?.context_length || 0);
      return Number.isFinite(value) && value > 0 ? value : null;
    }
    if (provider === 'ollama') {
      const models = Array.isArray(providerState?.models) ? providerState.models : [];
      const item = models.find((entry: any) => String(entry?.name || '').trim() === model);
      const value = Number(
        item?.context_length ||
        item?.num_ctx ||
        item?.details?.context_length ||
        item?.details?.num_ctx ||
        0
      );
      return Number.isFinite(value) && value > 0 ? value : null;
    }
    return null;
  }
}
