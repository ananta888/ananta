import { ConfigGraphNode, VIEW_IDS, ViewId } from '../models/config-graph.model';

// ── Interfaces ────────────────────────────────────────────────────────────────

export interface LayoutNode { id: string; x: number; y: number; w: number; h: number; node: ConfigGraphNode; }
export interface ViewMeta { id: ViewId; label: string; color: string; description: string; }
export interface CloneFormField { key: string; label: string; type: 'text' | 'select'; options?: string[]; hint?: string; }
export interface CloneFormState {
  sourceNode: ConfigGraphNode | null;
  entryType: 'agent_profile' | 'path_rule' | 'restricted_inference_model' | 'restricted_inference_task';
  mode: 'create' | 'clone' | 'edit';
  fields: CloneFormField[];
  values: Record<string, string>;
  saving: boolean;
  error: string | null;
}
export interface ConnectedNode { node: ConfigGraphNode; direction: 'out' | 'in'; edgeType: string; }
export type GraphStatusFilter = 'all' | 'active' | 'inactive' | 'diagnostics' | 'stale';

// ── Constants ─────────────────────────────────────────────────────────────────

export const NODE_W = 160, NODE_H = 44, COL_GAP = 200, ROW_GAP = 60;

export const VIEWS: ViewMeta[] = [
  { id: VIEW_IDS.configurationOverview, label: 'Gesamtübersicht', color: '#4A90D9', description: 'Alle Konfigurationsknoten und Beziehungen im Snapshot' },
  { id: VIEW_IDS.effectiveConfig,  label: 'Effektive Konfiguration', color: '#1976D2', description: 'Welche Nodes für eine Surface aktuell aktiv sind' },
  { id: VIEW_IDS.profileActivation, label: 'Profil-Aktivierung',     color: '#4CAF50', description: 'Agenten-Profile und deren Aktivierungspfade' },
  { id: VIEW_IDS.agentRuntime,     label: 'Agent-Laufzeit',          color: '#9C27B0', description: 'Agenten-Instanzen, Worker und Laufzeit-Konfiguration' },
  { id: VIEW_IDS.policyPath,       label: 'Policy-Pfad',             color: '#FF9800', description: 'Pfad-Regeln und KI-Modus-Einschränkungen' },
  { id: VIEW_IDS.planningFlow,     label: 'Planungs-Flow',           color: '#00BCD4', description: 'Planung, Templates und Goal-Erstellung' },
  { id: VIEW_IDS.contextPipeline,  label: 'Kontext-Pipeline',        color: '#CDDC39', description: 'Kontext-Quellen, CodeCompass und RAG-Konfiguration' },
];

export const VIEW_PRIMARY_TYPES: Partial<Record<ViewId, string[]>> = {
  [VIEW_IDS.configurationOverview]: [],
  [VIEW_IDS.profileActivation]: ['agent_profile'],
  [VIEW_IDS.policyPath]:        ['path_rule'],
  [VIEW_IDS.planningFlow]:      ['goal_template'],
  [VIEW_IDS.agentRuntime]:      ['model_provider', 'tool_group'],
  [VIEW_IDS.contextPipeline]:   ['context_source', 'codecompass_profile', 'rag_profile', 'embedding_model', 'restricted_inference', 'restricted_inference_model', 'restricted_inference_task', 'codecompass_ranking'],
  [VIEW_IDS.effectiveConfig]:   ['agent_profile', 'path_rule', 'goal_template', 'model_provider', 'embedding_model', 'restricted_inference', 'restricted_inference_model', 'restricted_inference_task', 'codecompass_ranking'],
};

export const CLONEABLE = new Set(['agent_profile', 'path_rule', 'restricted_inference_model', 'restricted_inference_task']);

export const PATH_CHARACTER_STYLES: Record<string, { label: string; color: string; bg: string }> = {
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

export const POLICY_PATH_SUGGESTIONS = [
  { glob: 'tests/**',          blocked: 'full_llm',              hint: 'Testdateien — LLM-Generierung einschränken' },
  { glob: 'docs/**',           blocked: 'code_gen',              hint: 'Dokumentation — keine Code-Generierung' },
  { glob: 'agent/services/**',  blocked: 'full_llm,direct_llm',  hint: 'Sensible Source-Pfade — nur eingeschränkte Analyse' },
  { glob: 'agent/routes/**',   blocked: 'full_llm',              hint: 'API-Routen — sicherheitskritisch' },
  { glob: 'agent/bootstrap/**',blocked: 'full_llm,code_gen',     hint: 'Bootstrap — nur lesende KI-Unterstützung' },
  { glob: '*.json',            blocked: 'free_text,code_generation', hint: 'Konfig-Dateien — kein Freitext' },
];

export const CLONE_DEFS: Record<string, CloneFormField[]> = {
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

