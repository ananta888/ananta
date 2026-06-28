import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';

import { FormsModule } from '@angular/forms';
import { Subject, takeUntil } from 'rxjs';

import { ConfigGraphService } from '../services/config-graph.service';
import {
  ConfigGraph,
  ConfigGraphEdge,
  ConfigGraphNode,
  PatchOp,
  ValidationResult,
  VIEW_IDS,
  ViewId,
  nodeColor,
} from '../models/config-graph.model';
import { ConfigGraphNodeDetailComponent } from './config-graph-node-detail.component';

// ── Interfaces ────────────────────────────────────────────────────────────────

interface LayoutNode { id: string; x: number; y: number; w: number; h: number; node: ConfigGraphNode; }
interface ViewMeta { id: ViewId; label: string; color: string; description: string; }
interface CloneFormField { key: string; label: string; type: 'text' | 'select'; options?: string[]; hint?: string; }
interface CloneFormState {
  sourceNode: ConfigGraphNode | null;
  entryType: 'agent_profile' | 'path_rule' | 'restricted_inference_model' | 'restricted_inference_task';
  mode: 'create' | 'clone' | 'edit';
  fields: CloneFormField[];
  values: Record<string, string>;
  saving: boolean;
  error: string | null;
}
interface ConnectedNode { node: ConfigGraphNode; direction: 'out' | 'in'; edgeType: string; }
type GraphStatusFilter = 'all' | 'active' | 'inactive' | 'diagnostics' | 'stale';

// ── Constants ─────────────────────────────────────────────────────────────────

const NODE_W = 160, NODE_H = 44, COL_GAP = 200, ROW_GAP = 60;

const VIEWS: ViewMeta[] = [
  { id: VIEW_IDS.configurationOverview, label: 'Gesamtübersicht', color: '#4A90D9', description: 'Alle Konfigurationsknoten und Beziehungen im Snapshot' },
  { id: VIEW_IDS.effectiveConfig,  label: 'Effektive Konfiguration', color: '#1976D2', description: 'Welche Nodes für eine Surface aktuell aktiv sind' },
  { id: VIEW_IDS.profileActivation, label: 'Profil-Aktivierung',     color: '#4CAF50', description: 'Agenten-Profile und deren Aktivierungspfade' },
  { id: VIEW_IDS.agentRuntime,     label: 'Agent-Laufzeit',          color: '#9C27B0', description: 'Agenten-Instanzen, Worker und Laufzeit-Konfiguration' },
  { id: VIEW_IDS.policyPath,       label: 'Policy-Pfad',             color: '#FF9800', description: 'Pfad-Regeln und KI-Modus-Einschränkungen' },
  { id: VIEW_IDS.planningFlow,     label: 'Planungs-Flow',           color: '#00BCD4', description: 'Planung, Templates und Goal-Erstellung' },
  { id: VIEW_IDS.contextPipeline,  label: 'Kontext-Pipeline',        color: '#CDDC39', description: 'Kontext-Quellen, CodeCompass und RAG-Konfiguration' },
];

const VIEW_PRIMARY_TYPES: Partial<Record<ViewId, string[]>> = {
  [VIEW_IDS.configurationOverview]: [],
  [VIEW_IDS.profileActivation]: ['agent_profile'],
  [VIEW_IDS.policyPath]:        ['path_rule'],
  [VIEW_IDS.planningFlow]:      ['goal_template'],
  [VIEW_IDS.agentRuntime]:      ['model_provider', 'tool_group'],
  [VIEW_IDS.contextPipeline]:   ['context_source', 'codecompass_profile', 'rag_profile', 'embedding_model', 'restricted_inference', 'restricted_inference_model', 'restricted_inference_task', 'codecompass_ranking'],
  [VIEW_IDS.effectiveConfig]:   ['agent_profile', 'path_rule', 'goal_template', 'model_provider', 'embedding_model', 'restricted_inference', 'restricted_inference_model', 'restricted_inference_task', 'codecompass_ranking'],
};

const CLONEABLE = new Set(['agent_profile', 'path_rule', 'restricted_inference_model', 'restricted_inference_task']);

const PATH_CHARACTER_STYLES: Record<string, { label: string; color: string; bg: string }> = {
  test:        { label: 'Testpfad',        color: '#fff', bg: '#006064' },
  analysis:    { label: 'Analysepfad',     color: '#fff', bg: '#4a148c' },
  ops:         { label: 'Betriebspfad',    color: '#fff', bg: '#bf360c' },
  maintenance: { label: 'Wartungspfad',    color: '#fff', bg: '#e65100' },
  creative:    { label: 'Entwicklungspfad',color: '#fff', bg: '#1b5e20' },
  explain:     { label: 'Erklärpfad',      color: '#fff', bg: '#0d47a1' },
  unknown:     { label: 'Allgemein',       color: '#aaa', bg: '#2a2a2a' },
  // rule characters
  kein_vollstaendiges_llm: { label: 'Kein Full-LLM', color: '#fff', bg: '#5a0000' },
  eingeschraenkt:          { label: 'Eingeschränkt',  color: '#fff', bg: '#3a2800' },
  selektiv_erlaubt:        { label: 'Selektiv',       color: '#fff', bg: '#1a2a3a' },
  offen:                   { label: 'Offen',          color: '#aaa', bg: '#2a2a2a' },
};

const POLICY_PATH_SUGGESTIONS = [
  { glob: 'tests/**',          blocked: 'full_llm',              hint: 'Testdateien — LLM-Generierung einschränken' },
  { glob: 'docs/**',           blocked: 'code_gen',              hint: 'Dokumentation — keine Code-Generierung' },
  { glob: 'agent/services/**',  blocked: 'full_llm,direct_llm',  hint: 'Sensible Source-Pfade — nur eingeschränkte Analyse' },
  { glob: 'agent/routes/**',   blocked: 'full_llm',              hint: 'API-Routen — sicherheitskritisch' },
  { glob: 'agent/bootstrap/**',blocked: 'full_llm,code_gen',     hint: 'Bootstrap — nur lesende KI-Unterstützung' },
  { glob: '*.json',            blocked: 'free_text,code_generation', hint: 'Konfig-Dateien — kein Freitext' },
];

const CLONE_DEFS: Record<string, CloneFormField[]> = {
  agent_profile: [
    { key: 'profile_id',         label: 'Neue Profil-ID',             type: 'text',   hint: 'Eindeutig, nur Buchstaben/Ziffern/_/-' },
    { key: 'primary_role',       label: 'Primäre Rolle',              type: 'text',   hint: 'z.B. code_writer, planner, reviewer' },
    { key: 'activation',         label: 'Aktivierungsbedingungen',     type: 'text',   hint: 'Kommagetrennt, z.B. surface:ai_snake_chat' },
    { key: 'allowed_task_kinds', label: 'Erlaubte Task-Arten',         type: 'text',   hint: 'Kommagetrennt, z.B. code, plan, research' },
    { key: 'code_change_policy', label: 'Code-Änderungs-Policy',       type: 'select', options: ['allowed', 'review_required', 'blocked'] },
    { key: 'context_policy_hint',label: 'Kontext-Policy-Hinweis',      type: 'text' },
  ],
  path_rule: [
    { key: 'path_glob',                  label: 'Pfad-Muster (Glob)',      type: 'text',   hint: 'z.B. agent/routes/** oder src/security/**' },
    { key: 'blocked_ai_modes',           label: 'Gesperrte KI-Modi',       type: 'text',   hint: 'Kommagetrennt: full_llm, restricted, code_gen' },
    { key: 'allowed_ai_modes',           label: 'Explizit erlaubte Modi',   type: 'text',   hint: 'Leer lassen = alle erlaubt (außer gesperrte)' },
    { key: 'allowed_model_engines',       label: 'Erlaubte Model-Engines',  type: 'text',   hint: 'Kommagetrennt: mock, pytorch, onnxruntime' },
    { key: 'allow_hidden_states',         label: 'Hidden States',           type: 'select', options: ['true', 'false'] },
    { key: 'allow_logits',                label: 'Logits',                  type: 'select', options: ['true', 'false'] },
    { key: 'allow_attention',             label: 'Attention',               type: 'select', options: ['true', 'false'] },
    { key: 'allow_free_text_generation', label: 'Freitext-Generierung',     type: 'select', options: ['true', 'false'] },
    { key: 'allow_tool_decision_from_model_text', label: 'Tool-Entscheidung aus Modelltext', type: 'select', options: ['true', 'false'] },
    { key: 'allow_code_generation',      label: 'Code-Generierung',         type: 'select', options: ['true', 'false'] },
    { key: 'require_controlled_write_policy', label: 'Controlled Write Policy', type: 'select', options: ['false', 'true'] },
    { key: 'llm_scope',                   label: 'LLM-Scope',               type: 'text' },
    { key: 'max_input_chars',             label: 'Max Input Chars',         type: 'text' },
    { key: 'max_batch_size',              label: 'Max Batch Size',          type: 'text' },
    { key: 'priority',                    label: 'Priorität',               type: 'text' },
  ],
  restricted_inference_model: [
    { key: 'id',         label: 'Model-ID',       type: 'text' },
    { key: 'engine',     label: 'Engine',         type: 'select', options: ['mock', 'sentence-transformers', 'huggingface-transformers', 'onnxruntime', 'pytorch'] },
    { key: 'model',      label: 'Modell',         type: 'text' },
    { key: 'revision',   label: 'Revision',       type: 'text' },
    { key: 'local_path', label: 'Lokaler Pfad',   type: 'text' },
    { key: 'device',     label: 'Device',         type: 'select', options: ['cpu', 'auto', 'cuda', 'mps'] },
    { key: 'enabled',    label: 'Aktiv',          type: 'select', options: ['true', 'false'] },
    { key: 'tasks',      label: 'Tasks',          type: 'text', hint: 'Kommagetrennt: candidate_rerank, task_classify, risk_score' },
  ],
  restricted_inference_task: [
    { key: 'id',                        label: 'Task-ID',               type: 'text' },
    { key: 'enabled',                   label: 'Aktiv',                 type: 'select', options: ['true', 'false'] },
    { key: 'preferred_engine',          label: 'Preferred Engine',      type: 'select', options: ['mock', 'sentence-transformers', 'huggingface-transformers', 'onnxruntime', 'pytorch'] },
    { key: 'fallback_to_deterministic', label: 'Deterministischer Fallback', type: 'select', options: ['true', 'false'] },
    { key: 'max_candidates',            label: 'Max Candidates',        type: 'text' },
    { key: 'labels',                    label: 'Labels',                type: 'text' },
    { key: 'weight',                    label: 'Gewicht',               type: 'text' },
  ],
  embedding_model: [
    { key: 'provider',               label: 'Provider',              type: 'select', options: ['local_hash', 'local', 'hash', 'fake', 'openai_compatible'] },
    { key: 'model',                  label: 'Modell',                type: 'text' },
    { key: 'model_version',          label: 'Modell-Version',        type: 'text' },
    { key: 'dimensions',             label: 'Dimensionen',           type: 'text' },
    { key: 'base_url',               label: 'Base URL',              type: 'text' },
    { key: 'timeout_seconds',        label: 'Timeout Sekunden',      type: 'text' },
    { key: 'external_calls_allowed', label: 'Externe Calls erlaubt', type: 'select', options: ['false', 'true'] },
    { key: 'allowed_base_urls',      label: 'Erlaubte Base URLs',    type: 'text' },
    { key: 'index_rebuild_policy',   label: 'Index Rebuild Policy',  type: 'select', options: ['on_change', 'manual', 'never'] },
    { key: 'diagnostics_enabled',    label: 'Diagnostics',           type: 'select', options: ['true', 'false'] },
  ],
  restricted_inference: [
    { key: 'enabled',                  label: 'Aktiv',                   type: 'select', options: ['true', 'false'] },
    { key: 'default_engine',           label: 'Default Engine',          type: 'select', options: ['sentence-transformers', 'mock', 'huggingface-transformers', 'onnxruntime', 'pytorch'] },
    { key: 'default_model_id',         label: 'Default Model-ID',        type: 'text' },
    { key: 'device',                   label: 'Device',                  type: 'select', options: ['cpu', 'auto', 'cuda', 'mps'] },
    { key: 'allow_mock_fallback',      label: 'Mock-Fallback',           type: 'select', options: ['true', 'false'] },
    { key: 'allowed_engines',          label: 'Erlaubte Engines',        type: 'text' },
    { key: 'transformer_feature_mode', label: 'Transformer-Feature-Modus', type: 'select', options: ['disabled', 'observe_only', 'context_first'] },
  ],
  codecompass_ranking: [
    { key: 'restricted_inference_rerank_enabled', label: 'RTIPM-Rerank', type: 'select', options: ['false', 'true'] },
    { key: 'embedding_score',                    label: 'Gewicht Embedding', type: 'text' },
    { key: 'graph_score',                        label: 'Gewicht Graph', type: 'text' },
    { key: 'symbol_score',                       label: 'Gewicht Symbol', type: 'text' },
    { key: 'transformer_rerank_score',           label: 'Gewicht Transformer', type: 'text' },
    { key: 'policy_penalty',                     label: 'Policy Penalty', type: 'text' },
    { key: 'trace_scores',                       label: 'Trace Scores', type: 'select', options: ['false', 'true'] },
    { key: 'fallback_without_model',             label: 'Fallback ohne Modell', type: 'select', options: ['true', 'false'] },
  ],
};

// ── Component ─────────────────────────────────────────────────────────────────

@Component({
  standalone: true,
  selector: 'app-config-graph-editor',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ConfigGraphNodeDetailComponent],
  template: "    <div class=\"cge-root\">\n    \n      <!-- Header -->\n      <div class=\"cge-header\">\n        <div class=\"cge-title-row\">\n          <h2 class=\"cge-title\">Visual Agent Configuration Graph</h2>\n          <div class=\"header-actions\">\n            <div class=\"mode-toggle\">\n              <button class=\"mode-btn\" [class.active]=\"displayMode==='config'\" (click)=\"setDisplayMode('config')\">☰ Konfiguration</button>\n              <button class=\"mode-btn\" [class.active]=\"displayMode==='graph'\"  (click)=\"setDisplayMode('graph')\">◈ Graph</button>\n            </div>\n            <button class=\"button-outline\" (click)=\"reload()\">↻ Aktualisieren</button>\n            @if (displayMode==='graph') {\n              <label class=\"edit-toggle\">\n                <input type=\"checkbox\" [(ngModel)]=\"editMode\" (ngModelChange)=\"cdr.markForCheck()\" /> Edit-Modus\n              </label>\n            }\n          </div>\n        </div>\n        @if ((graph?.diagnostics?.length ?? 0) > 0) {\n          <div class=\"diag-bar\">\n            @for (d of graph!.diagnostics; track d) {\n              <span class=\"diag-item\">⚠ {{ d }}</span>\n            }\n          </div>\n        }\n      </div>\n    \n      <!-- Body -->\n      <div class=\"cge-body\">\n    \n        <!-- Sidebar -->\n        <div class=\"cge-sidebar\">\n          <div class=\"sidebar-section-label\">Ansichten</div>\n          <div class=\"view-cards\">\n            @for (v of views; track v) {\n              <button class=\"view-card\" [class.active]=\"activeView===v.id\" (click)=\"setView(v.id)\">\n                <div class=\"vcard-dot\" [style.background]=\"v.color\"></div>\n                <div class=\"vcard-body\">\n                  <div class=\"vcard-title\">{{ v.label }}</div>\n                  <div class=\"vcard-desc\">{{ v.description }}</div>\n                  @if (graph) {\n                    <span class=\"count-badge\" [style.background]=\"activeView===v.id ? v.color : undefined\">\n                      {{ (graph.views[v.id] ?? []).length }} Nodes\n                    </span>\n                  }\n                </div>\n              </button>\n            }\n          </div>\n          <div class=\"sidebar-divider\"></div>\n          <div class=\"sidebar-section-label\">Graph filtern</div>\n          <div class=\"graph-filter-form\">\n            <input\n              [(ngModel)]=\"graphSearchText\"\n              (ngModelChange)=\"onGraphFilterChanged()\"\n              placeholder=\"Suche nach Label, Typ, Datei...\"\n              class=\"eff-input\"\n              />\n            <select\n              [(ngModel)]=\"graphNodeType\"\n              (ngModelChange)=\"onGraphFilterChanged()\"\n              class=\"eff-input\"\n              >\n              <option value=\"\">Alle Node-Typen</option>\n              @for (t of availableNodeTypes; track t) {\n                <option [value]=\"t\">{{ t }}</option>\n              }\n            </select>\n            <select\n              [(ngModel)]=\"graphStatus\"\n              (ngModelChange)=\"onGraphFilterChanged()\"\n              class=\"eff-input\"\n              >\n              <option value=\"all\">Alle Status</option>\n              <option value=\"active\">Nur aktiv</option>\n              <option value=\"inactive\">Nur inaktiv</option>\n              <option value=\"diagnostics\">Mit Diagnose</option>\n              <option value=\"stale\">Veraltet</option>\n            </select>\n            @if (hasGraphFilters) {\n              <button class=\"button-outline full-w\" (click)=\"clearGraphSearchFilters()\">Filter zurücksetzen</button>\n            }\n          </div>\n          <div class=\"sidebar-divider\"></div>\n          <div class=\"sidebar-section-label\">Effektiv auflösen</div>\n          <div class=\"effective-form\">\n            <input [(ngModel)]=\"effectiveSurface\" placeholder=\"Surface (z.B. ai_snake_chat)\" class=\"eff-input\" />\n            <input [(ngModel)]=\"effectiveTaskKind\" placeholder=\"Task-Kind (optional)\" class=\"eff-input\" />\n            <input [(ngModel)]=\"effectivePath\" placeholder=\"Pfad (optional)\" class=\"eff-input\" />\n            <button class=\"button-outline full-w\" (click)=\"resolveEffective()\">Auflösen →</button>\n          </div>\n          @if (graph) {\n            <div class=\"sidebar-footer\">\n              <span>{{ graph.node_count }} Nodes · {{ graph.edge_count }} Edges</span>\n              @if (graph.diagnostics.length) {\n                <span class=\"warn-inline\"> · {{ graph.diagnostics.length }} ⚠</span>\n              }\n            </div>\n          }\n        </div>\n    \n        <!-- Main -->\n        <div class=\"cge-main\">\n    \n          <!-- View header -->\n          @if (activeViewMeta) {\n            <div class=\"view-header\">\n              <div class=\"vhdot\" [style.background]=\"activeViewMeta.color\"></div>\n              <div class=\"vh-text\">\n                <div class=\"vh-title\">{{ activeViewMeta.label }}</div>\n                <div class=\"vh-desc\">{{ activeViewMeta.description }}</div>\n              </div>\n              @if (graph) {\n                <div class=\"vh-right\">\n                  <span class=\"count-badge\" [style.background]=\"activeViewMeta.color\">\n                    {{ visibleNodeIds.length }} / {{ graph.node_count }} Nodes\n                  </span>\n                  @if (hasGraphFilters) {\n                    <span class=\"filter-badge\" (click)=\"clearGraphSearchFilters()\">\n                      Suche aktiv x\n                    </span>\n                  }\n                  @if (graphFilterIds) {\n                    <span class=\"filter-badge\" (click)=\"clearGraphFilter()\">\n                      Fokus aktiv x\n                    </span>\n                  }\n                  <span class=\"snap-id muted\">{{ graph.snapshot_id }}</span>\n                </div>\n              }\n            </div>\n          }\n    \n          <!-- ══════════ CONFIG MODE ══════════ -->\n          @if (displayMode === 'config') {\n            <!-- Effective result -->\n            @if (effectiveResult) {\n              <div class=\"effective-panel\">\n                <div class=\"ep-header\">\n                  <strong>Effektiv: {{ effectiveResult.surface }}</strong>\n                  @if (effectiveResult.task_kind) {\n                    <span class=\"badge\">{{ effectiveResult.task_kind }}</span>\n                  }\n                  @if (effectiveResult.path) {\n                    <span class=\"badge\">{{ effectiveResult.path }}</span>\n                  }\n                  <button (click)=\"effectiveResult = null; cdr.markForCheck()\" class=\"close-btn\">✕</button>\n                </div>\n                <div class=\"ep-grid\">\n                  <div><div class=\"eff-label\">Profil</div>{{ effectiveResult.agent_profile?.['profile_id'] ?? '—' }}</div>\n                  <div><div class=\"eff-label\">Template</div>{{ effectiveResult.goal_template?.['template_id'] ?? '—' }}</div>\n                  <div>\n                    <div class=\"eff-label\">Gesperrte Modi</div>\n                    @for (m of effectiveResult.effective_ai_modes_blocked; track m) {\n                      <span class=\"tag warn\">{{ m }}</span>\n                    }\n                    @if (!effectiveResult.effective_ai_modes_blocked.length) {\n                      <span class=\"muted\">keine</span>\n                    }\n                  </div>\n                  <div>\n                    <div class=\"eff-label\">Erlaubte Modi</div>\n                    @for (m of effectiveResult.effective_ai_modes_allowed; track m) {\n                      <span class=\"tag ok\">{{ m }}</span>\n                    }\n                    @if (!effectiveResult.effective_ai_modes_allowed.length) {\n                      <span class=\"muted\">alle</span>\n                    }\n                  </div>\n                  @if (effectiveResult.warnings.length) {\n                    <div class=\"ep-span2\">\n                      <div class=\"eff-label\">Warnungen</div>\n                      <ul class=\"warn-list\">@for (w of effectiveResult.warnings; track w) {\n                        <li>{{ w }}</li>\n                      }</ul>\n                    </div>\n                  }\n                </div>\n              </div>\n            }\n            <!-- effectiveConfig empty hint -->\n            @if (activeView === VIEW_IDS.effectiveConfig && !effectiveResult) {\n              <div class=\"config-hint\">\n                <div class=\"config-hint-icon\">◈</div>\n                <div>\n                  <strong>Effektive Konfiguration auflösen</strong>\n                  <p class=\"muted\">Surface und optionalen Task-Kind in der Sidebar eingeben und \"Auflösen\" klicken.</p>\n                </div>\n              </div>\n            }\n            <!-- Config panel -->\n            @if (activeView !== VIEW_IDS.effectiveConfig || effectiveResult) {\n              <div class=\"config-panel\">\n                <!-- Breadcrumb (detail mode) -->\n                @if (selectedConfigItem && !cloneState) {\n                  <div class=\"cp-breadcrumb\">\n                    <button class=\"breadcrumb-back\" (click)=\"clearItemSelection()\">← Übersicht</button>\n                    <span class=\"breadcrumb-sep\">/</span>\n                    <span class=\"breadcrumb-dot\" [style.background]=\"activeViewMeta?.color\"></span>\n                    <span class=\"breadcrumb-label\">{{ selectedConfigItem.label }}</span>\n                  </div>\n                }\n                <!-- Overview header -->\n                @if (!selectedConfigItem && !cloneState) {\n                  <div class=\"cp-header\">\n                    <span class=\"cp-count\">{{ configPanelItems.length }} {{ activeViewMeta?.label }}-Einträge</span>\n                    @if (creatableTypeForView) {\n                      <button class=\"button-outline\" (click)=\"startNewEntry()\">+ Neu erstellen</button>\n                    }\n                  </div>\n                }\n                <!-- Clone form header (breadcrumb variant) -->\n                @if (cloneState) {\n                  <div class=\"cp-breadcrumb\">\n                    <button class=\"breadcrumb-back\" (click)=\"cancelClone()\">← Zurück</button>\n                    <span class=\"breadcrumb-sep\">/</span>\n                    <span class=\"breadcrumb-label\">\n                      {{ cloneState.mode === 'edit' && cloneState.sourceNode ? 'Bearbeiten: ' + cloneState.sourceNode.label : cloneState.sourceNode ? 'Klonen: ' + cloneState.sourceNode.label : 'Neu: ' + cloneState.entryType }}\n                    </span>\n                  </div>\n                }\n                <!-- ── OVERVIEW: card grid ── -->\n                @if (!selectedConfigItem && !cloneState && !loading) {\n                  <div class=\"config-cards\">\n                    @for (item of configPanelItems; track item) {\n                      <div\n                        class=\"config-card selectable\"\n                        [class.card-inactive]=\"!item.runtime_active\"\n                        [class.card-has-diags]=\"item.diagnostics.length > 0\"\n                        (click)=\"selectConfigItem(item)\">\n                        <div class=\"card-head\">\n                          <div class=\"card-dot\" [style.background]=\"activeViewMeta?.color\"></div>\n                          <strong class=\"card-label\" [title]=\"item.id\">{{ item.label }}</strong>\n                          @if (characterBadge(item); as badge) {\n                            <span class=\"char-badge\" [style.background]=\"badge.bg\" [style.color]=\"badge.color\">{{ badge.label }}</span>\n                          }\n                          @if (!item.runtime_active) {\n                            <span class=\"inactive-tag\">inaktiv</span>\n                          }\n                        </div>\n                        <div class=\"card-fields\">\n                          @for (f of keyFieldsFor(item); track f) {\n                            <div class=\"card-field\">\n                              <span class=\"cf-label\">{{ f.label }}</span>\n                              <span class=\"cf-value\" [class.cf-empty]=\"!f.value || f.value==='—'\">{{ f.value }}</span>\n                            </div>\n                          }\n                        </div>\n                        @if (item.diagnostics.length > 0) {\n                          <div class=\"card-diags\">\n                            @for (d of item.diagnostics; track d) {\n                              <span class=\"diag-item\">⚠ {{ d }}</span>\n                            }\n                          </div>\n                        }\n                        <div class=\"card-open-hint\">Klicken zum Öffnen →</div>\n                      </div>\n                    }\n                    <!-- Policy-Pfad: empty state with suggestions -->\n                    @if (configPanelItems.length === 0 && activeView === VIEW_IDS.policyPath) {\n                      <div class=\"policy-empty\">\n                        <div class=\"policy-empty-head\">\n                          <span class=\"policy-empty-icon\">⚖</span>\n                          <div>\n                            <strong>Keine Pfad-Regeln konfiguriert</strong>\n                            <p class=\"muted\">Alle Pfade sind offen — kein KI-Modus ist eingeschränkt. Typische Beispiele:</p>\n                          </div>\n                        </div>\n                        <div class=\"policy-suggestions\">\n                          @for (s of policySuggestions; track s) {\n                            <div class=\"policy-suggestion\" (click)=\"prefillSuggestion(s)\">\n                              <div class=\"sug-glob\">{{ s.glob }}</div>\n                              <div class=\"sug-blocked\"><span class=\"char-badge\" style=\"background:#5a0000;color:#fff\">{{ s.blocked }}</span></div>\n                              <div class=\"sug-hint muted\">{{ s.hint }}</div>\n                              <div class=\"sug-action\">Als Vorlage →</div>\n                            </div>\n                          }\n                        </div>\n                      </div>\n                    }\n                    @if (configPanelItems.length === 0 && activeView !== VIEW_IDS.policyPath) {\n                      <div class=\"cp-empty\">\n                        <p class=\"muted\">Keine Einträge für diese Ansicht konfiguriert.</p>\n                      </div>\n                    }\n                  </div>\n                }\n                @if (loading && !selectedConfigItem && !cloneState) {\n                  <div class=\"loading-wrap\">\n                    <p class=\"muted\">Wird geladen…</p>\n                  </div>\n                }\n                <!-- ── DETAIL VIEW ── -->\n                @if (selectedConfigItem && !cloneState) {\n                  <div class=\"config-detail\">\n                    <div class=\"detail-head\">\n                      <div class=\"detail-type-dot\" [style.background]=\"activeViewMeta?.color\"></div>\n                      <div class=\"detail-head-text\">\n                        <h3 class=\"detail-title\">{{ selectedConfigItem.label }}</h3>\n                        <div class=\"detail-meta\">\n                          <span class=\"card-type\">{{ selectedConfigItem.node_type }}</span>\n                          @if (characterBadge(selectedConfigItem); as badge) {\n                            <span class=\"char-badge\" [style.background]=\"badge.bg\" [style.color]=\"badge.color\">{{ badge.label }}</span>\n                          }\n                          @if (!selectedConfigItem.runtime_active) {\n                            <span class=\"inactive-tag\">inaktiv</span>\n                          }\n                          <span class=\"detail-id muted\">{{ selectedConfigItem.id }}</span>\n                        </div>\n                      </div>\n                      <div class=\"detail-head-actions\">\n                        @if (isEditableConfigNode(selectedConfigItem)) {\n                          <button class=\"button-outline\" (click)=\"startEdit(selectedConfigItem)\">\n                            Bearbeiten\n                          </button>\n                        }\n                        @if (isCloneable(selectedConfigItem)) {\n                          <button class=\"button-outline\" (click)=\"startClone(selectedConfigItem)\">\n                            ⎘ Klonen & anpassen\n                          </button>\n                        }\n                        <button class=\"button-outline\" (click)=\"showInGraph(selectedConfigItem)\">Im Graph zeigen</button>\n                      </div>\n                    </div>\n                    <!-- ── BEHAVIOR DIMENSIONS (agent_profile only) ── -->\n                    @if (selectedConfigItem.node_type === 'agent_profile' && behaviorDims(selectedConfigItem); as beh) {\n                      <div class=\"detail-section\">\n                        <div class=\"section-label\">Verhaltens-Dimensionen</div>\n                        <div class=\"beh-grid\">\n                          <!-- Execute contract -->\n                          <div class=\"beh-card\" [class]=\"'beh-gate-' + beh.execute_contract.gate\">\n                            <div class=\"beh-card-head\">\n                              <span class=\"beh-icon\">{{ beh.execute_contract.gate === 'blocked' ? '🔒' : beh.execute_contract.gate === 'explicit_approval_required' ? '⚠' : '✓' }}</span>\n                              <div>\n                                <div class=\"beh-card-title\">Ausführungs-Vertrag</div>\n                                <div class=\"beh-card-value\">{{ beh.execute_contract.label }}</div>\n                              </div>\n                            </div>\n                            <div class=\"beh-card-desc\">{{ beh.execute_contract.description }}</div>\n                            <div class=\"beh-caps\">\n                              <span class=\"beh-cap\" [class.beh-cap-yes]=\"beh.execute_contract.can_write_files\" [class.beh-cap-no]=\"!beh.execute_contract.can_write_files\">\n                                {{ beh.execute_contract.can_write_files ? '✓' : '✗' }} Dateien schreiben\n                              </span>\n                              <span class=\"beh-cap\" [class.beh-cap-yes]=\"beh.execute_contract.can_run_commands\" [class.beh-cap-no]=\"!beh.execute_contract.can_run_commands\">\n                                {{ beh.execute_contract.can_run_commands ? '✓' : '✗' }} Befehle ausführen\n                              </span>\n                            </div>\n                          </div>\n                          <!-- Context authority -->\n                          <div class=\"beh-card beh-card-context\">\n                            <div class=\"beh-card-head\">\n                              <span class=\"beh-icon\">◈</span>\n                              <div>\n                                <div class=\"beh-card-title\">Kontext-Autorität</div>\n                                <div class=\"beh-card-value\">{{ beh.context_authority.label }}</div>\n                              </div>\n                            </div>\n                            <div class=\"beh-card-desc\">{{ beh.context_authority.description }}</div>\n                            <div class=\"beh-sources\">\n                              @for (s of beh.context_authority.primary_sources; track s) {\n                                <span class=\"beh-source\">{{ s }}</span>\n                              }\n                              <span class=\"beh-cc\" [class.beh-cc-primary]=\"beh.context_authority.codecompass === 'primary'\" [class.beh-cc-secondary]=\"beh.context_authority.codecompass === 'secondary'\">\n                                CodeCompass: {{ beh.context_authority.codecompass }}\n                              </span>\n                            </div>\n                          </div>\n                          <!-- Must-not -->\n                          @if (beh.must_not?.length) {\n                            <div class=\"beh-card beh-card-mustnot\">\n                              <div class=\"beh-card-head\">\n                                <span class=\"beh-icon\">🚫</span>\n                                <div>\n                                  <div class=\"beh-card-title\">Darf nicht</div>\n                                  <div class=\"beh-card-value\">{{ beh.scope_label }}</div>\n                                </div>\n                              </div>\n                              <ul class=\"beh-mustnot-list\">\n                                @for (mn of beh.must_not; track mn) {\n                                  <li>{{ mn }}</li>\n                                }\n                              </ul>\n                            </div>\n                          }\n                        </div>\n                      </div>\n                    }\n                    <div class=\"detail-section\">\n                      <div class=\"section-label\">Konfiguration</div>\n                      <div class=\"detail-fields\">\n                        @for (f of configFieldsFor(selectedConfigItem); track f) {\n                          <div class=\"detail-field\">\n                            <span class=\"df-label\">{{ f.label }}</span>\n                            <span class=\"df-value\" [class.df-empty]=\"f.value==='—'\">{{ f.value }}</span>\n                          </div>\n                        }\n                      </div>\n                    </div>\n                    @if (connectedNodes.length > 0) {\n                      <div class=\"detail-section\">\n                        <div class=\"section-label\">Verbundene Nodes ({{ connectedNodes.length }})</div>\n                        <div class=\"connected-list\">\n                          @for (cn of connectedNodes; track cn) {\n                            <div class=\"connected-node\">\n                              <span class=\"cn-dir\" [class.cn-out]=\"cn.direction==='out'\" [class.cn-in]=\"cn.direction==='in'\">\n                                {{ cn.direction === 'out' ? '→' : '←' }}\n                              </span>\n                              <span class=\"cn-edge-type\">{{ cn.edgeType }}</span>\n                              <div class=\"cn-dot\" [style.background]=\"nodeTypeColor(cn.node.node_type)\"></div>\n                              <strong class=\"cn-label\">{{ cn.node.label }}</strong>\n                              <span class=\"card-type\">{{ cn.node.node_type }}</span>\n                              <button class=\"button-outline cn-open-btn\" (click)=\"selectConfigItem(cn.node)\">Öffnen</button>\n                            </div>\n                          }\n                        </div>\n                      </div>\n                    }\n                    @if (selectedConfigItem.diagnostics.length > 0) {\n                      <div class=\"detail-section\">\n                        <div class=\"section-label\">Diagnosen</div>\n                        <div class=\"card-diags\">\n                          @for (d of selectedConfigItem.diagnostics; track d) {\n                            <span class=\"diag-item\">⚠ {{ d }}</span>\n                          }\n                        </div>\n                      </div>\n                    }\n                  </div>\n                }\n                <!-- ── CLONE / CREATE FORM ── -->\n                @if (cloneState) {\n                  <div class=\"clone-form\">\n                    @if (cloneState.sourceNode) {\n                      <div class=\"cf-source-hint\">\n                        Vorausgefüllt aus: <em>{{ cloneState.sourceNode.label }}</em> — Felder anpassen und speichern.\n                      </div>\n                    }\n                    <div class=\"cf-fields\">\n                      @for (f of cloneState.fields; track f) {\n                        <div class=\"cf-field\">\n                          <label class=\"cf-field-label\">\n                            {{ f.label }}\n                            @if (f.key==='profile_id' || f.key==='path_glob' || f.key==='id') {\n                              <span class=\"required-mark\">*</span>\n                            }\n                          </label>\n                          @if (f.type==='select') {\n                            <select [(ngModel)]=\"cloneState.values[f.key]\" class=\"cf-input\">\n                              @for (o of f.options; track o) {\n                                <option [value]=\"o\">{{ o }}</option>\n                              }\n                            </select>\n                          }\n                          @if (f.type==='text') {\n                            <input [(ngModel)]=\"cloneState.values[f.key]\" class=\"cf-input\" />\n                          }\n                          @if (f.hint) {\n                            <div class=\"cf-hint\">{{ f.hint }}</div>\n                          }\n                        </div>\n                      }\n                    </div>\n                    @if (cloneState.error) {\n                      <div class=\"cf-error\">{{ cloneState.error }}</div>\n                    }\n                    <div class=\"cf-actions\">\n                      <button class=\"button-primary\" (click)=\"saveClone()\" [disabled]=\"cloneState.saving\">\n                        {{ cloneState.saving ? 'Wird gespeichert…' : cloneState.mode === 'edit' ? 'Änderung vormerken' : 'Speichern' }}\n                      </button>\n                      <button class=\"button-outline\" (click)=\"cancelClone()\">Abbrechen</button>\n                    </div>\n                  </div>\n                }\n              </div>\n            }\n          }\n    \n          <!-- ══════════ GRAPH MODE ══════════ -->\n          @if (displayMode === 'graph') {\n            @if (effectiveResult) {\n              <div class=\"effective-panel\">\n                <div class=\"ep-header\">\n                  <strong>Effektiv: {{ effectiveResult.surface }}</strong>\n                  @if (effectiveResult.task_kind) {\n                    <span class=\"badge\">{{ effectiveResult.task_kind }}</span>\n                  }\n                  <button (click)=\"effectiveResult = null; cdr.markForCheck()\" class=\"close-btn\">✕</button>\n                </div>\n              </div>\n            }\n            @if (editMode && pendingOps.length > 0) {\n              <div class=\"edit-toolbar\">\n                <span>{{ pendingOps.length }} Änderung(en)</span>\n                <button class=\"button-outline\" (click)=\"validatePatch()\">Validieren</button>\n                @if (lastValidation?.requires_approval) {\n                  <input [(ngModel)]=\"approvalToken\" class=\"approval-input\" placeholder=\"Approval-Token\" />\n                }\n                <button class=\"button-outline\" [disabled]=\"!lastValidation?.valid\" (click)=\"applyPatch()\">Anwenden</button>\n                <button class=\"button-outline danger\" (click)=\"discardPatch()\">Verwerfen</button>\n                @if (lastValidation) {\n                  <span class=\"risk-badge\" [class]=\"'risk-' + lastValidation.risk_tier\">{{ lastValidation.risk_tier }}</span>\n                }\n                @if (lastValidation?.errors?.length) {\n                  <ul class=\"edit-errors\">\n                    @for (e of lastValidation!.errors; track e) {\n                      <li>{{ e }}</li>\n                    }\n                  </ul>\n                }\n              </div>\n            }\n            @if (lastSourceDiffs.length) {\n              <div class=\"source-diff-panel\">\n                <div class=\"source-diff-head\">\n                  <strong>Source-Diff</strong>\n                  @if (lastRollbackArtifact) {\n                    <button class=\"button-outline\" (click)=\"rollbackLastPatch()\">Rollback</button>\n                  }\n                </div>\n                @for (diff of lastSourceDiffs; track diff) {\n                  <pre>{{ diff }}</pre>\n                }\n              </div>\n            }\n            @if (!loading) {\n              <div class=\"cge-canvas-wrap\">\n                <svg #svgEl class=\"cge-svg\" [attr.width]=\"svgWidth\" [attr.height]=\"svgHeight\" (click)=\"onSvgClick($event)\">\n                  <defs>\n                    <marker id=\"arrow\" markerWidth=\"8\" markerHeight=\"8\" refX=\"6\" refY=\"3\" orient=\"auto\">\n                      <path d=\"M0,0 L0,6 L8,3 z\" fill=\"#666\" />\n                    </marker>\n                  </defs>\n                  <g class=\"edges-layer\">\n                    @for (edge of visibleEdges; track edge) {\n                      <line\n                        [attr.x1]=\"edgeX1(edge)\" [attr.y1]=\"edgeY1(edge)\"\n                        [attr.x2]=\"edgeX2(edge)\" [attr.y2]=\"edgeY2(edge)\"\n                        stroke=\"#555\" stroke-width=\"1.5\" marker-end=\"url(#arrow)\" />\n                    }\n                  </g>\n                  <g class=\"nodes-layer\">\n                    @for (ln of visibleLayoutNodes; track ln) {\n                      <g\n                        class=\"graph-node\"\n                        [class.selected]=\"selectedNode?.id === ln.id\"\n                        [class.stale]=\"ln.node.stale\"\n                        [class.inactive]=\"!ln.node.runtime_active\"\n                        (click)=\"selectNode($event, ln.node)\"\n                        style=\"cursor:pointer\">\n                        <rect [attr.x]=\"ln.x\" [attr.y]=\"ln.y\" [attr.width]=\"ln.w\" [attr.height]=\"ln.h\"\n                          rx=\"6\" [attr.fill]=\"nodeColor(ln.node.node_type)\"\n                          [attr.fill-opacity]=\"ln.node.runtime_active ? 0.85 : 0.35\"\n                          [attr.stroke]=\"selectedNode?.id===ln.id ? '#fff' : 'transparent'\" stroke-width=\"2\" />\n                        <text [attr.x]=\"ln.x+ln.w/2\" [attr.y]=\"ln.y+ln.h/2-4\"\n                        text-anchor=\"middle\" font-size=\"10\" fill=\"#fff\" font-weight=\"600\" style=\"pointer-events:none\">{{ ln.node.node_type }}</text>\n                        <text [attr.x]=\"ln.x+ln.w/2\" [attr.y]=\"ln.y+ln.h/2+10\"\n                        text-anchor=\"middle\" font-size=\"11\" fill=\"#fff\" style=\"pointer-events:none;dominant-baseline:middle\">{{ truncate(ln.node.label,18) }}</text>\n                        @if (ln.node.diagnostics.length>0) {\n                          <circle [attr.cx]=\"ln.x+ln.w-6\" [attr.cy]=\"ln.y+6\" r=\"5\" fill=\"#ff8f00\" />\n                        }\n                      </g>\n                    }\n                  </g>\n                </svg>\n                @if (visibleLayoutNodes.length===0) {\n                  <div class=\"empty-view\"><p class=\"muted\">Keine Nodes in dieser Ansicht.</p></div>\n                }\n              </div>\n            } @else {\n              <div class=\"loading-wrap\"><p class=\"muted\">Graph wird geladen…</p></div>\n            }\n            <app-config-graph-node-detail\n              [node]=\"selectedNode\" [editMode]=\"editMode\"\n              (closed)=\"selectedNode=null; cdr.markForCheck()\"\n              (removeRequested)=\"queueRemoveNode($event)\" />\n          }\n    \n        </div>\n      </div>\n    </div>\n    ",
  styles: ["    /* ── Root / Header ─────────────────────────────────────── */\n    .cge-root { display:flex; flex-direction:column; height:100%; box-sizing:border-box; font-size:13px; background:var(--bg,#111); color:var(--text,#ddd); }\n    .cge-header { padding:10px 16px 8px; border-bottom:1px solid var(--border-color,#2a2a2a); display:flex; flex-direction:column; gap:6px; flex-shrink:0; }\n    .cge-title-row { display:flex; align-items:center; gap:12px; }\n    .cge-title { margin:0; font-size:15px; font-weight:600; flex:1; }\n    .header-actions { display:flex; gap:8px; align-items:center; }\n    .mode-toggle { display:flex; border:1px solid var(--border-color,#444); border-radius:6px; overflow:hidden; }\n    .mode-btn { padding:4px 12px; background:transparent; border:none; cursor:pointer; color:var(--text,#ccc); font-size:12px; font-weight:500; }\n    .mode-btn.active { background:var(--primary,#4A90D9); color:#fff; }\n    .edit-toggle { display:flex; align-items:center; gap:5px; cursor:pointer; font-size:12px; }\n    .diag-bar { display:flex; gap:8px; flex-wrap:wrap; background:#2a1400; border-radius:6px; padding:6px 10px; }\n    .diag-item { font-size:11px; color:#ffcc80; }\n\n    /* ── Body / Sidebar ─────────────────────────────────────── */\n    .cge-body { display:flex; flex:1; min-height:0; overflow:hidden; }\n    .cge-sidebar { width:222px; min-width:222px; border-right:1px solid var(--border-color,#2a2a2a); display:flex; flex-direction:column; overflow-y:auto; background:var(--bg-sidebar,#161616); flex-shrink:0; }\n    .sidebar-section-label { padding:10px 12px 3px; font-size:10px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--text-muted,#666); }\n    .sidebar-divider { border-top:1px solid var(--border-color,#2a2a2a); margin:6px 0; }\n    .sidebar-footer { margin-top:auto; padding:8px 12px; font-size:11px; color:var(--text-muted,#666); border-top:1px solid var(--border-color,#2a2a2a); }\n\n    /* View cards */\n    .view-cards { display:flex; flex-direction:column; gap:2px; padding:4px 7px; }\n    .view-card { display:flex; align-items:flex-start; gap:9px; padding:8px 9px; border-radius:7px; border:1px solid transparent; background:transparent; cursor:pointer; color:var(--text,#ccc); text-align:left; width:100%; transition:background .1s; }\n    .view-card:hover { background:var(--bg-hover,#1e1e1e); }\n    .view-card.active { background:var(--bg-selected,#1a2a3a); border-color:#4A90D9; }\n    .vcard-dot { width:9px; height:9px; border-radius:50%; flex-shrink:0; margin-top:3px; }\n    .vcard-body { display:flex; flex-direction:column; gap:2px; flex:1; min-width:0; }\n    .vcard-title { font-size:12px; font-weight:600; line-height:1.3; }\n    .vcard-desc { font-size:10px; color:var(--text-muted,#888); line-height:1.3; }\n    .count-badge { display:inline-block; font-size:10px; border-radius:8px; padding:1px 6px; background:var(--bg-badge,#2a2a2a); color:#fff; font-weight:600; margin-top:3px; opacity:.85; }\n\n    /* Effective form */\n    .effective-form { display:flex; flex-direction:column; gap:5px; padding:5px 9px 8px; }\n    .graph-filter-form { display:flex; flex-direction:column; gap:5px; padding:5px 9px 8px; }\n    .eff-input { width:100%; box-sizing:border-box; font-size:12px; padding:5px 8px; border-radius:5px; border:1px solid var(--border-color,#333); background:var(--bg-input,#1e1e1e); color:var(--text,#ccc); }\n    .full-w { width:100%; }\n\n    /* ── Main ─────────────────────────────────────────────────── */\n    .cge-main { flex:1; display:flex; flex-direction:column; min-width:0; overflow:hidden; }\n\n    /* View header */\n    .view-header { display:flex; align-items:center; gap:10px; padding:9px 14px; border-bottom:1px solid var(--border-color,#2a2a2a); background:var(--bg-sidebar,#161616); flex-shrink:0; }\n    .vhdot { width:11px; height:11px; border-radius:50%; flex-shrink:0; }\n    .vh-text { flex:1; }\n    .vh-title { font-size:13px; font-weight:600; }\n    .vh-desc { font-size:11px; color:var(--text-muted,#888); }\n    .vh-right { display:flex; align-items:center; gap:8px; }\n    .filter-badge { font-size:11px; background:#3a2800; color:#ffcc80; border-radius:8px; padding:2px 8px; cursor:pointer; }\n    .snap-id { font-size:10px; font-family:monospace; }\n\n    /* Effective panel */\n    .effective-panel { margin:10px 14px 0; padding:12px; border-radius:8px; border:1px solid var(--border-color,#333); background:var(--bg-card,#1a1a1a); flex-shrink:0; }\n    .ep-header { display:flex; gap:8px; align-items:center; margin-bottom:8px; font-size:13px; }\n    .ep-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px 16px; font-size:12px; }\n    .ep-span2 { grid-column:span 2; }\n    .eff-label { font-size:10px; color:var(--text-muted,#888); text-transform:uppercase; letter-spacing:.05em; margin-bottom:2px; }\n    .badge { background:var(--bg-input,#2a2a2a); border-radius:8px; padding:1px 7px; font-size:11px; }\n    .tag { display:inline-block; border-radius:3px; padding:1px 5px; font-size:11px; margin:1px; }\n    .tag.warn { background:#4a1a00; color:#ffcc80; }\n    .tag.ok { background:#1b3a20; color:#a5d6a7; }\n    .warn-list { margin:4px 0 0 16px; padding:0; font-size:12px; }\n    .close-btn { background:none; border:none; cursor:pointer; color:var(--text-muted,#888); font-size:14px; margin-left:auto; padding:0 4px; }\n\n    /* Config hint */\n    .config-hint { display:flex; gap:16px; align-items:flex-start; margin:24px 16px; padding:18px 20px; border-radius:10px; border:1px dashed var(--border-color,#333); background:var(--bg-card,#1a1a1a); }\n    .config-hint-icon { font-size:28px; color:var(--text-muted,#555); flex-shrink:0; margin-top:2px; }\n    .config-hint p { margin:4px 0 0; }\n\n    /* ── Config panel ─────────────────────────────────────────── */\n    .config-panel { display:flex; flex-direction:column; flex:1; overflow:hidden; }\n\n    /* Breadcrumb */\n    .cp-breadcrumb { display:flex; align-items:center; gap:8px; padding:9px 14px; border-bottom:1px solid var(--border-color,#2a2a2a); background:var(--bg-sidebar,#161616); flex-shrink:0; font-size:12px; }\n    .breadcrumb-back { background:none; border:none; cursor:pointer; color:var(--primary,#4A90D9); font-size:12px; padding:0; }\n    .breadcrumb-back:hover { text-decoration:underline; }\n    .breadcrumb-sep { color:var(--text-muted,#555); }\n    .breadcrumb-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }\n    .breadcrumb-label { font-weight:600; color:var(--text,#ddd); }\n\n    /* Overview header */\n    .cp-header { display:flex; align-items:center; justify-content:space-between; padding:10px 14px 6px; flex-shrink:0; }\n    .cp-count { font-size:12px; color:var(--text-muted,#888); }\n    .cp-empty { padding:24px 14px; text-align:center; }\n\n    /* Card grid */\n    .config-cards { flex:1; overflow-y:auto; padding:8px 12px 12px; display:flex; flex-direction:column; gap:6px; }\n    .config-card { border-radius:9px; border:1px solid var(--border-color,#2c2c2c); background:var(--bg-card,#1a1a1a); padding:11px 13px; transition:border-color .12s, background .12s; }\n    .config-card.selectable { cursor:pointer; }\n    .config-card.selectable:hover { border-color:var(--primary,#4A90D9); background:var(--bg-hover,#1e1e1e); }\n    .config-card.card-inactive { opacity:.55; }\n    .config-card.card-has-diags { border-color:#5a3500; }\n    .card-head { display:flex; align-items:center; gap:8px; margin-bottom:8px; }\n    .card-dot { width:9px; height:9px; border-radius:50%; flex-shrink:0; }\n    .card-label { font-size:13px; flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }\n    .card-type { font-size:10px; background:var(--bg-input,#252525); border-radius:4px; padding:1px 6px; color:var(--text-muted,#888); white-space:nowrap; flex-shrink:0; }\n    .inactive-tag { font-size:10px; background:#3a1a00; color:#ff8f00; border-radius:4px; padding:1px 6px; flex-shrink:0; }\n    .card-fields { display:grid; grid-template-columns:1fr 1fr; gap:4px 12px; margin-bottom:6px; }\n    .card-field { display:flex; flex-direction:column; gap:1px; }\n    .cf-label { font-size:10px; color:var(--text-muted,#777); text-transform:uppercase; letter-spacing:.04em; }\n    .cf-value { font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }\n    .cf-value.cf-empty { color:var(--text-muted,#555); }\n    .card-diags { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:6px; }\n    .card-open-hint { font-size:10px; color:var(--text-muted,#555); text-align:right; margin-top:2px; }\n    .char-badge { font-size:10px; border-radius:4px; padding:2px 7px; white-space:nowrap; font-weight:600; flex-shrink:0; }\n\n    /* Policy-Pfad empty state */\n    .policy-empty { padding:16px 14px; display:flex; flex-direction:column; gap:14px; }\n    .policy-empty-head { display:flex; gap:14px; align-items:flex-start; }\n    .policy-empty-icon { font-size:26px; flex-shrink:0; color:var(--text-muted,#555); }\n    .policy-empty-head p { margin:4px 0 0; font-size:12px; }\n    .policy-suggestions { display:flex; flex-direction:column; gap:5px; }\n    .policy-suggestion { display:grid; grid-template-columns:180px 160px 1fr auto; align-items:center; gap:8px; padding:8px 12px; border-radius:7px; border:1px solid var(--border-color,#2a2a2a); background:var(--bg-card,#1a1a1a); cursor:pointer; font-size:12px; }\n    .policy-suggestion:hover { border-color:var(--primary,#4A90D9); }\n    .sug-glob { font-family:monospace; font-size:12px; color:var(--text,#ddd); }\n    .sug-hint { font-size:11px; }\n    .sug-action { font-size:11px; color:var(--primary,#4A90D9); white-space:nowrap; }\n\n    /* ── Detail view ─────────────────────────────────────────── */\n    .config-detail { flex:1; overflow-y:auto; padding:14px; display:flex; flex-direction:column; gap:14px; }\n    .detail-head { display:flex; align-items:flex-start; gap:12px; padding-bottom:12px; border-bottom:1px solid var(--border-color,#2a2a2a); }\n    .detail-type-dot { width:14px; height:14px; border-radius:50%; flex-shrink:0; margin-top:4px; }\n    .detail-head-text { flex:1; }\n    .detail-title { margin:0 0 4px; font-size:16px; font-weight:700; }\n    .detail-meta { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }\n    .detail-id { font-size:10px; font-family:monospace; }\n    .detail-head-actions { display:flex; gap:6px; flex-wrap:wrap; align-items:flex-start; }\n\n    .detail-section { display:flex; flex-direction:column; gap:8px; }\n    .section-label { font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:var(--text-muted,#666); padding-bottom:4px; border-bottom:1px solid var(--border-color,#222); }\n    .detail-fields { display:grid; grid-template-columns:max-content 1fr; gap:6px 20px; font-size:12px; }\n    .detail-field { display:contents; }\n    .df-label { color:var(--text-muted,#888); font-size:11px; align-self:start; padding-top:1px; white-space:nowrap; }\n    .df-value { word-break:break-word; }\n    .df-value.df-empty { color:var(--text-muted,#555); }\n\n    /* Connected nodes */\n    .connected-list { display:flex; flex-direction:column; gap:5px; }\n    .connected-node { display:flex; align-items:center; gap:8px; padding:6px 10px; border-radius:6px; background:var(--bg-card,#1a1a1a); border:1px solid var(--border-color,#2a2a2a); font-size:12px; }\n    .cn-dir { width:16px; text-align:center; font-size:14px; font-weight:700; flex-shrink:0; }\n    .cn-dir.cn-out { color:#4A90D9; }\n    .cn-dir.cn-in  { color:#9C27B0; }\n    .cn-edge-type { font-size:10px; background:var(--bg-input,#252525); border-radius:4px; padding:1px 5px; color:var(--text-muted,#888); white-space:nowrap; }\n    .cn-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }\n    .cn-label { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }\n    .cn-open-btn { font-size:11px; padding:2px 8px; flex-shrink:0; }\n\n    /* ── Clone form ──────────────────────────────────────────── */\n    .clone-form { padding:14px; display:flex; flex-direction:column; gap:10px; overflow-y:auto; flex:1; }\n    .cf-source-hint { font-size:11px; color:var(--text-muted,#888); padding:7px 10px; background:var(--bg-input,#1e1e1e); border-radius:5px; }\n    .cf-fields { display:grid; grid-template-columns:1fr 1fr; gap:10px 16px; }\n    .cf-field { display:flex; flex-direction:column; gap:4px; }\n    .cf-field-label { font-size:11px; font-weight:600; color:var(--text-muted,#aaa); }\n    .required-mark { color:#ff8f00; margin-left:2px; }\n    .cf-input { padding:6px 8px; border-radius:5px; border:1px solid var(--border-color,#333); background:var(--bg-input,#1e1e1e); color:var(--text,#ddd); font-size:12px; width:100%; box-sizing:border-box; }\n    .cf-hint { font-size:10px; color:var(--text-muted,#666); }\n    .cf-error { color:#ff8a80; font-size:12px; padding:6px 10px; background:#2a0000; border-radius:5px; }\n    .cf-actions { display:flex; gap:8px; }\n    .button-primary { padding:6px 16px; border-radius:5px; border:none; background:var(--primary,#4A90D9); color:#fff; font-size:12px; cursor:pointer; font-weight:600; }\n    .button-primary:hover { background:#3a7fc9; }\n    .button-primary:disabled { opacity:.4; cursor:default; }\n\n    /* ── Graph mode ──────────────────────────────────────────── */\n    .cge-canvas-wrap { flex:1; overflow:auto; margin:10px 12px 8px; border:1px solid var(--border-color,#2a2a2a); border-radius:8px; background:var(--bg-canvas,#0e0e0e); }\n    .cge-svg { display:block; }\n    .graph-node.selected rect { stroke:#fff !important; stroke-width:2 !important; }\n    .graph-node.inactive { opacity:.4; }\n    .graph-node.stale rect { stroke:#ff8f00 !important; stroke-width:1.5 !important; stroke-dasharray:4 3; }\n    .empty-view, .loading-wrap { display:flex; justify-content:center; align-items:center; min-height:280px; }\n    .edit-toolbar { display:flex; align-items:center; gap:10px; padding:7px 14px; margin:8px 12px 0; border-radius:7px; border:1px solid var(--border-color,#333); background:var(--bg-card,#1a1a1a); flex-wrap:wrap; font-size:12px; flex-shrink:0; }\n    .risk-badge { border-radius:8px; padding:2px 7px; font-size:11px; }\n    .risk-low { background:#1b3a20; color:#a5d6a7; }\n    .risk-medium { background:#5a2500; color:#ffcc80; }\n    .risk-high, .risk-critical { background:#5a0000; color:#ff8a80; }\n    .warn-inline { color:#ffcc80; font-size:12px; }\n    .edit-errors { color:#ff8a80; font-size:12px; margin:0; padding:0 0 0 14px; }\n    button.danger { color:#ff8a80; }\n    .approval-input { width:220px; background:var(--bg-input,#1e1e1e); color:var(--text,#ddd); border:1px solid var(--border-color,#333); border-radius:5px; padding:5px 8px; font-size:12px; }\n    .source-diff-panel { margin:8px 12px 0; border:1px solid var(--border-color,#333); border-radius:7px; background:var(--bg-card,#1a1a1a); padding:10px; max-height:220px; overflow:auto; font-size:12px; }\n    .source-diff-head { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:8px; }\n    .source-diff-panel pre { white-space:pre-wrap; margin:0 0 8px; color:#c8d8e8; font-size:11px; }\n\n    /* ── Behavior dimensions ────────────────────────────────────── */\n    .beh-grid { display:flex; flex-direction:column; gap:8px; }\n    .beh-card { border-radius:8px; border:1px solid var(--border-color,#2a2a2a); padding:11px 13px; display:flex; flex-direction:column; gap:7px; }\n    .beh-card-head { display:flex; align-items:flex-start; gap:10px; }\n    .beh-icon { font-size:18px; flex-shrink:0; margin-top:1px; line-height:1; }\n    .beh-card-title { font-size:10px; text-transform:uppercase; letter-spacing:.07em; color:var(--text-muted,#777); margin-bottom:2px; }\n    .beh-card-value { font-size:13px; font-weight:700; color:var(--text,#ddd); }\n    .beh-card-desc { font-size:12px; color:var(--text-muted,#aaa); line-height:1.5; }\n    /* Gate-specific card tints */\n    .beh-gate-blocked { background:#1a0000; border-color:#4a0000; }\n    .beh-gate-explicit_approval_required { background:#1a1000; border-color:#4a3000; }\n    .beh-gate-hub_validated { background:#001a08; border-color:#004a18; }\n    .beh-card-context { background:#0d1a2a; border-color:#1a3a5a; }\n    .beh-card-mustnot { background:#1a0a00; border-color:#4a2000; }\n    /* Capability pills */\n    .beh-caps { display:flex; gap:6px; flex-wrap:wrap; }\n    .beh-cap { font-size:11px; border-radius:4px; padding:2px 8px; font-weight:600; }\n    .beh-cap-yes { background:#003a10; color:#66bb6a; }\n    .beh-cap-no  { background:#2a0000; color:#ef9a9a; }\n    /* Context sources */\n    .beh-sources { display:flex; gap:5px; flex-wrap:wrap; align-items:center; }\n    .beh-source { font-size:11px; background:var(--bg-input,#1e2a3a); border-radius:4px; padding:2px 7px; color:#90caf9; font-family:monospace; }\n    .beh-cc { font-size:11px; border-radius:4px; padding:2px 7px; font-weight:600; }\n    .beh-cc-primary   { background:#0d47a1; color:#fff; }\n    .beh-cc-secondary { background:#2a2a2a; color:#aaa; }\n    /* Must-not list */\n    .beh-mustnot-list { margin:0; padding-left:16px; display:flex; flex-direction:column; gap:4px; }\n    .beh-mustnot-list li { font-size:12px; color:#ef9a9a; line-height:1.4; }\n\n    /* ── Shared utils ─────────────────────────────────────────── */\n    .muted { color:var(--text-muted,#666); }\n    .button-outline { padding:5px 11px; border-radius:5px; border:1px solid var(--border-color,#444); background:transparent; cursor:pointer; color:var(--text,#ccc); font-size:12px; }\n    .button-outline:hover { background:var(--bg-hover,#222); }\n    .button-outline:disabled { opacity:.4; cursor:default; }\n  "],
})
export class ConfigGraphEditorComponent implements OnInit, OnDestroy {
  private readonly svc = inject(ConfigGraphService);
  readonly cdr = inject(ChangeDetectorRef);
  private readonly destroy$ = new Subject<void>();

  @ViewChild('svgEl') svgEl!: ElementRef<SVGSVGElement>;

  readonly views = VIEWS;
  readonly VIEW_IDS = VIEW_IDS;
  readonly nodeColor = nodeColor;

  graph: ConfigGraph | null = null;
  loading = true;
  activeView: ViewId = VIEW_IDS.configurationOverview;
  selectedNode: ConfigGraphNode | null = null;
  selectedConfigItem: ConfigGraphNode | null = null;
  displayMode: 'config' | 'graph' = 'config';
  editMode = false;
  graphFilterIds: string[] | null = null;
  graphSearchText = '';
  graphNodeType = '';
  graphStatus: GraphStatusFilter = 'all';

  effectiveSurface = 'ai_snake_chat';
  effectiveTaskKind = '';
  effectivePath = '';
  effectiveResult: import('../models/config-graph.model').EffectiveConfig | null = null;

  pendingOps: PatchOp[] = [];
  lastValidation: ValidationResult | null = null;
  approvalToken = '';
  lastSourceDiffs: string[] = [];
  lastRollbackArtifact: Record<string, unknown> | null = null;
  cloneState: CloneFormState | null = null;

  private layoutNodes: Map<string, LayoutNode> = new Map();
  svgWidth = 1200;
  svgHeight = 800;

  // ── Getters ────────────────────────────────────────────────────────────────

  get activeViewMeta(): ViewMeta | null {
    return VIEWS.find(v => v.id === this.activeView) ?? null;
  }

  get availableNodeTypes(): string[] {
    if (!this.graph) return [];
    return Array.from(new Set(Object.values(this.graph.nodes).map(n => n.node_type))).sort();
  }

  get hasGraphFilters(): boolean {
    return Boolean(this.graphSearchText.trim() || this.graphNodeType || this.graphStatus !== 'all');
  }

  get visibleNodeIds(): string[] {
    if (!this.graph) return [];
    const all = (this.graph.views[this.activeView] ?? []).filter(id => id in this.graph!.nodes);
    const focusIds = this.graphFilterIds ? new Set(this.graphFilterIds) : null;
    return all.filter(id => {
      const node = this.graph!.nodes[id];
      return (!focusIds || focusIds.has(id)) && this.matchesGraphFilters(node);
    });
  }

  get visibleLayoutNodes(): LayoutNode[] {
    return this.visibleNodeIds.map(id => this.layoutNodes.get(id)!).filter(Boolean);
  }

  get visibleEdges(): ConfigGraphEdge[] {
    if (!this.graph) return [];
    const vis = new Set(this.visibleNodeIds);
    return this.graph.edges.filter(e => vis.has(e.source) && vis.has(e.target));
  }

  get configPanelItems(): ConfigGraphNode[] {
    if (!this.graph) return [];
    return this.visibleNodeIds.map(id => this.graph!.nodes[id]);
  }

  get creatableTypeForView(): 'agent_profile' | 'path_rule' | 'restricted_inference_model' | 'restricted_inference_task' | null {
    const types = VIEW_PRIMARY_TYPES[this.activeView] ?? [];
    if (types.includes('agent_profile')) return 'agent_profile';
    if (types.includes('path_rule')) return 'path_rule';
    if (types.includes('restricted_inference_model')) return 'restricted_inference_model';
    if (types.includes('restricted_inference_task')) return 'restricted_inference_task';
    return null;
  }

  get connectedNodes(): ConnectedNode[] {
    if (!this.graph || !this.selectedConfigItem) return [];
    const nid = this.selectedConfigItem.id;
    const seen = new Set<string>();
    const result: ConnectedNode[] = [];
    for (const e of this.graph.edges) {
      if (e.source === nid && e.target in this.graph.nodes && !seen.has(e.target)) {
        seen.add(e.target);
        result.push({ node: this.graph.nodes[e.target], direction: 'out', edgeType: e.edge_type });
      }
      if (e.target === nid && e.source in this.graph.nodes && !seen.has(e.source)) {
        seen.add(e.source);
        result.push({ node: this.graph.nodes[e.source], direction: 'in', edgeType: e.edge_type });
      }
    }
    return result;
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  ngOnInit(): void { this.reload(); }
  ngOnDestroy(): void { this.destroy$.next(); this.destroy$.complete(); }

  // ── Navigation ─────────────────────────────────────────────────────────────

  reload(): void {
    this.loading = true;
    this.selectedNode = null;
    this.selectedConfigItem = null;
    this.effectiveResult = null;
    this.cloneState = null;
    this.graphFilterIds = null;
    this.cdr.markForCheck();
    this.svc.getGraph().pipe(takeUntil(this.destroy$)).subscribe({
      next: g => { this.graph = g; this.computeLayout(); this.loading = false; this.cdr.markForCheck(); },
      error: () => { this.loading = false; this.cdr.markForCheck(); },
    });
  }

  setView(v: ViewId): void {
    this.activeView = v;
    this.selectedNode = null;
    this.selectedConfigItem = null;
    this.cloneState = null;
    this.graphFilterIds = null;
    this.computeLayout();
    this.cdr.markForCheck();
  }

  setDisplayMode(m: 'config' | 'graph'): void {
    this.displayMode = m;
    if (m === 'config') { this.graphFilterIds = null; }
    else { this.computeLayout(); }
    this.cdr.markForCheck();
  }

  clearGraphFilter(): void {
    this.graphFilterIds = null;
    this.computeLayout();
    this.cdr.markForCheck();
  }

  onGraphFilterChanged(): void {
    this.selectedNode = null;
    this.selectedConfigItem = null;
    this.computeLayout();
    this.cdr.markForCheck();
  }

  clearGraphSearchFilters(): void {
    this.graphSearchText = '';
    this.graphNodeType = '';
    this.graphStatus = 'all';
    this.onGraphFilterChanged();
  }

  // ── Config item selection ──────────────────────────────────────────────────

  selectConfigItem(node: ConfigGraphNode): void {
    this.selectedConfigItem = node;
    this.cloneState = null;
    this.cdr.markForCheck();
  }

  clearItemSelection(): void {
    this.selectedConfigItem = null;
    this.cloneState = null;
    this.cdr.markForCheck();
  }

  showInGraph(node: ConfigGraphNode): void {
    this.displayMode = 'graph';
    this.selectedNode = node;
    // Find the best view that contains this node
    for (const v of VIEWS) {
      if ((this.graph?.views[v.id] ?? []).includes(node.id)) {
        this.activeView = v.id;
        break;
      }
    }
    // Filter to node + direct neighbors
    const neighbors = this.getNeighborIds(node.id);
    this.graphFilterIds = [node.id, ...neighbors];
    this.computeLayout();
    this.cdr.markForCheck();
  }

  isPrimaryTypeInView(node: ConfigGraphNode): boolean {
    const types = VIEW_PRIMARY_TYPES[this.activeView] ?? [];
    return types.includes(node.node_type);
  }

  private matchesGraphFilters(node: ConfigGraphNode): boolean {
    if (this.graphNodeType && node.node_type !== this.graphNodeType) return false;
    if (this.graphStatus === 'active' && !node.runtime_active) return false;
    if (this.graphStatus === 'inactive' && node.runtime_active) return false;
    if (this.graphStatus === 'diagnostics' && node.diagnostics.length === 0) return false;
    if (this.graphStatus === 'stale' && !node.stale) return false;

    const query = this.graphSearchText.trim().toLowerCase();
    if (!query) return true;
    const haystack = [
      node.id,
      node.node_type,
      node.label,
      node.source_file ?? '',
      node.runtime_source ?? '',
      ...Object.entries(node.data as Record<string, unknown>).flatMap(([k, v]) => [
        k,
        Array.isArray(v) ? v.join(' ') : typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? ''),
      ]),
    ].join(' ').toLowerCase();
    return haystack.includes(query);
  }

  // ── Field helpers ──────────────────────────────────────────────────────────

  keyFieldsFor(node: ConfigGraphNode): { label: string; value: string }[] {
    const d = node.data as Record<string, unknown>;
    const arr = (k: string) => (Array.isArray(d[k]) ? (d[k] as string[]).join(', ') : '') || '—';
    const str = (k: string) => String(d[k] ?? '') || '—';
    switch (node.node_type) {
      case 'agent_profile': return [
        { label: 'Rolle', value: str('primary_role') },
        { label: 'Aktivierung', value: arr('activation') },
        { label: 'Task-Arten', value: arr('allowed_task_kinds') },
        { label: 'Code-Policy', value: str('code_change_policy') },
      ];
      case 'path_rule': return [
        { label: 'Muster', value: str('path_glob') },
        { label: 'Gesperrt', value: arr('blocked_ai_modes') },
        { label: 'Erlaubt', value: arr('allowed_ai_modes') },
      ];
      case 'goal_template': return [{ label: 'Beschreibung', value: str('description') }];
      case 'model_provider': return [{ label: 'Backend', value: str('backend') }];
      case 'tool_group': return [{ label: 'Gruppe', value: str('group') }];
      case 'embedding_model': return [{ label: 'Provider', value: str('provider') }];
      default: return Object.entries(d).slice(0, 3).map(([k, v]) => ({ label: k, value: Array.isArray(v) ? (v as string[]).join(', ') : String(v ?? '—') }));
    }
  }

  allFieldsFor(node: ConfigGraphNode): { label: string; value: string }[] {
    const fmt = (v: unknown): string => {
      if (Array.isArray(v)) return (v as string[]).join(', ') || '—';
      if (v == null || v === '') return '—';
      return String(v);
    };
    return Object.entries(node.data as Record<string, unknown>).map(([k, v]) => ({ label: k, value: fmt(v) }));
  }

  configFieldsFor(node: ConfigGraphNode): { label: string; value: string }[] {
    const SKIP = new Set(['behavior_dimensions', 'path_character', 'path_character_label', 'rule_character']);
    const fmt = (v: unknown): string => {
      if (Array.isArray(v)) return (v as string[]).join(', ') || '—';
      if (v == null || v === '') return '—';
      if (typeof v === 'object') return JSON.stringify(v);
      return String(v);
    };
    return Object.entries(node.data as Record<string, unknown>)
      .filter(([k]) => !SKIP.has(k))
      .map(([k, v]) => ({ label: k, value: fmt(v) }));
  }

  behaviorDims(node: ConfigGraphNode): Record<string, any> | null {
    const d = node.data as Record<string, unknown>;
    const beh = d['behavior_dimensions'];
    if (!beh || typeof beh !== 'object') return null;
    return beh as Record<string, any>;
  }

  nodeTypeColor(nodeType: string): string {
    return nodeColor(nodeType);
  }

  isCloneable(node: ConfigGraphNode): boolean {
    return CLONEABLE.has(node.node_type);
  }

  isEditableConfigNode(node: ConfigGraphNode): boolean {
    return node.writable && Boolean(CLONE_DEFS[node.node_type]);
  }

  readonly policySuggestions = POLICY_PATH_SUGGESTIONS;

  characterBadge(node: ConfigGraphNode): { label: string; color: string; bg: string } | null {
    const d = node.data as Record<string, unknown>;
    const key = (d['path_character'] ?? d['rule_character']) as string | undefined;
    if (!key || key === 'unknown' || key === 'offen') return null;
    return PATH_CHARACTER_STYLES[key] ?? null;
  }

  prefillSuggestion(s: { glob: string; blocked: string; hint: string }): void {
    const fields = CLONE_DEFS['path_rule'] ?? [];
    const values: Record<string, string> = {};
    for (const f of fields) values[f.key] = f.type === 'select' && f.options?.length ? f.options[0] : '';
    values['path_glob'] = s.glob;
    values['blocked_ai_modes'] = s.blocked;
    this.cloneState = { sourceNode: null, entryType: 'path_rule', mode: 'create', fields, values, saving: false, error: null };
    this.cdr.markForCheck();
  }

  // ── Clone / Create ─────────────────────────────────────────────────────────

  startClone(source: ConfigGraphNode): void {
    const entryType = source.node_type as 'agent_profile' | 'path_rule' | 'restricted_inference_model' | 'restricted_inference_task';
    const fields = CLONE_DEFS[entryType] ?? [];
    const values: Record<string, string> = {};
    const d = source.data as Record<string, unknown>;
    for (const f of fields) {
      const raw = d[f.key];
      values[f.key] = Array.isArray(raw) ? (raw as string[]).join(', ') : String(raw ?? '');
    }
    if (entryType === 'agent_profile') values['profile_id'] = '';
    if (entryType === 'path_rule') values['path_glob'] = '';
    if (entryType === 'restricted_inference_model') values['id'] = '';
    if (entryType === 'restricted_inference_task') values['id'] = '';
    this.cloneState = { sourceNode: source, entryType, mode: 'clone', fields, values, saving: false, error: null };
    this.cdr.markForCheck();
  }

  startEdit(source: ConfigGraphNode): void {
    const entryType = source.node_type as CloneFormState['entryType'];
    const fields = CLONE_DEFS[entryType] ?? [];
    const values = this.formValuesForNode(source, fields);
    this.cloneState = { sourceNode: source, entryType, mode: 'edit', fields, values, saving: false, error: null };
    this.cdr.markForCheck();
  }

  startNewEntry(): void {
    const entryType = this.creatableTypeForView;
    if (!entryType) return;
    const fields = CLONE_DEFS[entryType] ?? [];
    const values: Record<string, string> = {};
    for (const f of fields) values[f.key] = f.type === 'select' && f.options?.length ? f.options[0] : '';
    this.cloneState = { sourceNode: null, entryType, mode: 'create', fields, values, saving: false, error: null };
    this.cdr.markForCheck();
  }

  saveClone(): void {
    if (!this.cloneState) return;
    const { entryType, values, sourceNode, mode } = this.cloneState;
    const data: Record<string, unknown> = this.normalizedFormData(entryType, values);
    if (mode === 'edit' && sourceNode) {
      this.pendingOps.push({ op: 'set_data', target: sourceNode.id, data });
      this.lastValidation = null;
      this.cloneState = null;
      this.selectedConfigItem = { ...sourceNode, data: { ...sourceNode.data, ...data } };
      this.cdr.markForCheck();
      return;
    }
    this.cloneState.saving = true;
    this.cloneState.error = null;
    this.cdr.markForCheck();
    this.svc.createConfigEntry(entryType, data).pipe(takeUntil(this.destroy$)).subscribe({
      next: g => {
        this.graph = g;
        this.cloneState = null;
        this.selectedConfigItem = null;
        this.computeLayout();
        this.cdr.markForCheck();
      },
      error: e => {
        if (this.cloneState) { this.cloneState.error = e?.error?.error ?? 'Speichern fehlgeschlagen'; this.cloneState.saving = false; }
        this.cdr.markForCheck();
      },
    });
  }

  private normalizedFormData(entryType: string, values: Record<string, string>): Record<string, unknown> {
    const data: Record<string, unknown> = { ...values };
    for (const k of ['activation', 'allowed_task_kinds', 'blocked_ai_modes', 'allowed_ai_modes', 'allowed_model_engines', 'tasks', 'labels']) {
      data[k] = String(data[k] ?? '').split(',').map((s: string) => s.trim()).filter(Boolean);
    }
    for (const k of ['allowed_base_urls', 'allowed_engines']) {
      if (k in data) data[k] = String(data[k] ?? '').split(',').map((s: string) => s.trim()).filter(Boolean);
    }
    for (const k of [
      'allow_hidden_states',
      'allow_logits',
      'allow_attention',
      'allow_free_text_generation',
      'allow_tool_decision_from_model_text',
      'allow_code_generation',
      'require_controlled_write_policy',
      'enabled',
      'fallback_to_deterministic',
      'external_calls_allowed',
      'diagnostics_enabled',
      'restricted_inference_rerank_enabled',
      'trace_scores',
      'fallback_without_model',
      'allow_mock_fallback',
    ]) {
      if (k in data) data[k] = data[k] !== 'false';
    }
    for (const k of ['max_input_chars', 'max_batch_size', 'priority', 'max_candidates', 'dimensions', 'timeout_seconds']) {
      if (!(k in data)) continue;
      const parsed = Number.parseInt(String(data[k] ?? '0'), 10);
      data[k] = Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
    }
    for (const k of ['weight']) {
      if (!(k in data)) continue;
      const parsed = Number.parseFloat(String(data[k] ?? '1'));
      data[k] = Number.isFinite(parsed) ? parsed : 1;
    }
    if (entryType === 'codecompass_ranking') {
      const scoreKeys = ['embedding_score', 'graph_score', 'symbol_score', 'transformer_rerank_score', 'policy_penalty'];
      const scoreWeights: Record<string, number> = {};
      for (const key of scoreKeys) {
        const parsed = Number.parseFloat(String(data[key] ?? '0'));
        scoreWeights[key] = Number.isFinite(parsed) ? parsed : 0;
        delete data[key];
      }
      data['score_weights'] = scoreWeights;
    }
    return data;
  }

  private formValuesForNode(node: ConfigGraphNode, fields: CloneFormField[]): Record<string, string> {
    const values: Record<string, string> = {};
    const data = node.data as Record<string, unknown>;
    const weights = (data['score_weights'] && typeof data['score_weights'] === 'object')
      ? data['score_weights'] as Record<string, unknown>
      : {};
    for (const field of fields) {
      const raw = field.key in weights ? weights[field.key] : data[field.key];
      values[field.key] = Array.isArray(raw) ? raw.join(', ') : String(raw ?? '');
      if (!values[field.key] && field.type === 'select' && field.options?.length) {
        values[field.key] = field.options[0];
      }
    }
    return values;
  }

  cancelClone(): void { this.cloneState = null; this.cdr.markForCheck(); }

  // ── Effective config ───────────────────────────────────────────────────────

  resolveEffective(): void {
    if (!this.effectiveSurface.trim()) return;
    this.svc.getEffectiveConfig({
      surface: this.effectiveSurface.trim(),
      task_kind: this.effectiveTaskKind.trim() || null,
      path: this.effectivePath.trim() || null,
    }).pipe(takeUntil(this.destroy$)).subscribe({
      next: ec => {
        this.effectiveResult = ec;
        if (this.graph && ec.effective_node_ids.length) {
          this.activeView = VIEW_IDS.effectiveConfig;
          this.graph.views[VIEW_IDS.effectiveConfig] = ec.effective_node_ids;
          this.computeLayout();
        }
        this.cdr.markForCheck();
      },
    });
  }

  // ── Patch ──────────────────────────────────────────────────────────────────

  queueRemoveNode(nodeId: string): void { this.pendingOps.push({ op: 'remove_node', target: nodeId, data: {} }); this.lastValidation = null; this.cdr.markForCheck(); }

  validatePatch(): void {
    if (!this.pendingOps.length) return;
    this.svc.validatePatch(this.pendingOps).pipe(takeUntil(this.destroy$)).subscribe({
      next: r => { this.lastValidation = r; this.cdr.markForCheck(); },
    });
  }

  applyPatch(): void {
    if (!this.pendingOps.length || !this.lastValidation?.valid) return;
    this.svc.applyPatch(this.pendingOps, this.approvalToken).pipe(takeUntil(this.destroy$)).subscribe({
      next: r => {
        this.graph = r.graph;
        this.pendingOps = [];
        this.lastValidation = null;
        this.approvalToken = '';
        this.selectedNode = null;
        this.lastSourceDiffs = (r.result.source_diffs ?? [])
          .map(item => String((item as Record<string, unknown>)['diff'] ?? ''))
          .filter(Boolean);
        this.lastRollbackArtifact = r.result.rollback_artifact ?? null;
        this.computeLayout();
        this.cdr.markForCheck();
      },
    });
  }

  rollbackLastPatch(): void {
    if (!this.lastRollbackArtifact) return;
    this.svc.rollbackPatch(this.lastRollbackArtifact).pipe(takeUntil(this.destroy$)).subscribe({
      next: r => {
        this.graph = r.graph;
        this.lastSourceDiffs = (r.result.source_diffs ?? [])
          .map(item => String((item as Record<string, unknown>)['diff'] ?? ''))
          .filter(Boolean);
        this.lastRollbackArtifact = r.result.rollback_artifact ?? null;
        this.computeLayout();
        this.cdr.markForCheck();
      },
    });
  }

  discardPatch(): void {
    this.pendingOps = [];
    this.lastValidation = null;
    this.approvalToken = '';
    this.cdr.markForCheck();
  }

  // ── SVG / Layout ───────────────────────────────────────────────────────────

  private computeLayout(): void {
    if (!this.graph) return;
    this.layoutNodes.clear();
    const ids = this.visibleNodeIds;
    const cols = Math.max(1, Math.ceil(Math.sqrt(ids.length)));
    let maxX = 0, maxY = 0;
    ids.forEach((id, i) => {
      const col = i % cols, row = Math.floor(i / cols);
      const x = 24 + col * (NODE_W + COL_GAP), y = 24 + row * (NODE_H + ROW_GAP);
      maxX = Math.max(maxX, x + NODE_W + 24); maxY = Math.max(maxY, y + NODE_H + 24);
      this.layoutNodes.set(id, { id, x, y, w: NODE_W, h: NODE_H, node: this.graph!.nodes[id] });
    });
    this.svgWidth = Math.max(800, maxX);
    this.svgHeight = Math.max(600, maxY);
  }

  private getNeighborIds(nodeId: string): string[] {
    if (!this.graph) return [];
    const neighbors = new Set<string>();
    for (const e of this.graph.edges) {
      if (e.source === nodeId && e.target in this.graph.nodes) neighbors.add(e.target);
      if (e.target === nodeId && e.source in this.graph.nodes) neighbors.add(e.source);
    }
    return Array.from(neighbors);
  }

  edgeX1(e: ConfigGraphEdge): number { return (this.layoutNodes.get(e.source)?.x ?? 0) + NODE_W; }
  edgeY1(e: ConfigGraphEdge): number { const ln = this.layoutNodes.get(e.source); return ln ? ln.y + NODE_H / 2 : 0; }
  edgeX2(e: ConfigGraphEdge): number { return this.layoutNodes.get(e.target)?.x ?? 0; }
  edgeY2(e: ConfigGraphEdge): number { const ln = this.layoutNodes.get(e.target); return ln ? ln.y + NODE_H / 2 : 0; }

  selectNode(event: MouseEvent, node: ConfigGraphNode): void { event.stopPropagation(); this.selectedNode = node; this.cdr.markForCheck(); }
  onSvgClick(_: MouseEvent): void { this.selectedNode = null; this.cdr.markForCheck(); }
  truncate(text: string, max: number): string { return text.length > max ? text.slice(0, max - 1) + '…' : text; }
}
