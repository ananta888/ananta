import { ConfigGraphNode, nodeColor } from '../models/config-graph.model';
import { CLONEABLE, CLONE_DEFS, PATH_CHARACTER_STYLES, type CloneFormField } from './config-graph-editor.models';

export interface ConfigDisplayField { label: string; value: string }

const formatValue = (value: unknown): string => {
  if (Array.isArray(value)) return value.join(', ') || '—';
  if (value == null || value === '') return '—';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
};

export function keyFieldsFor(node: ConfigGraphNode): ConfigDisplayField[] {
  const data = node.data as Record<string, unknown>;
  const array = (key: string) => Array.isArray(data[key]) ? (data[key] as string[]).join(', ') || '—' : '—';
  const string = (key: string) => String(data[key] ?? '') || '—';
  switch (node.node_type) {
    case 'agent_profile': return [
      { label: 'Rolle', value: string('primary_role') },
      { label: 'Aktivierung', value: array('activation') },
      { label: 'Task-Arten', value: array('allowed_task_kinds') },
      { label: 'Code-Policy', value: string('code_change_policy') },
    ];
    case 'path_rule': return [
      { label: 'Muster', value: string('path_glob') },
      { label: 'Gesperrt', value: array('blocked_ai_modes') },
      { label: 'Erlaubt', value: array('allowed_ai_modes') },
    ];
    case 'goal_template': return [{ label: 'Beschreibung', value: string('description') }];
    case 'model_provider': return [{ label: 'Backend', value: string('backend') }];
    case 'tool_group': return [{ label: 'Gruppe', value: string('group') }];
    case 'embedding_model': return [{ label: 'Provider', value: string('provider') }];
    default: return Object.entries(data).slice(0, 3).map(([label, value]) => ({ label, value: formatValue(value) }));
  }
}

export function allFieldsFor(node: ConfigGraphNode): ConfigDisplayField[] {
  return Object.entries(node.data as Record<string, unknown>)
    .map(([label, value]) => ({ label, value: formatValue(value) }));
}

export function configFieldsFor(node: ConfigGraphNode): ConfigDisplayField[] {
  const skip = new Set(['behavior_dimensions', 'path_character', 'path_character_label', 'rule_character']);
  return Object.entries(node.data as Record<string, unknown>)
    .filter(([key]) => !skip.has(key))
    .map(([label, value]) => ({ label, value: formatValue(value) }));
}

export function behaviorDimensions(node: ConfigGraphNode): Record<string, any> | null {
  const dimensions = (node.data as Record<string, unknown>)['behavior_dimensions'];
  return dimensions && typeof dimensions === 'object' ? dimensions as Record<string, any> : null;
}

export function characterBadge(node: ConfigGraphNode): { label: string; color: string; bg: string } | null {
  const data = node.data as Record<string, unknown>;
  const key = (data['path_character'] ?? data['rule_character']) as string | undefined;
  return !key || ['unknown', 'offen'].includes(key) ? null : PATH_CHARACTER_STYLES[key] ?? null;
}

export const nodeTypeColor = nodeColor;
export const isCloneable = (node: ConfigGraphNode): boolean => CLONEABLE.has(node.node_type);
export const isEditableConfigNode = (node: ConfigGraphNode): boolean => node.writable && Boolean(CLONE_DEFS[node.node_type]);

export function formValuesForNode(node: ConfigGraphNode, fields: CloneFormField[]): Record<string, string> {
  const values: Record<string, string> = {};
  const data = node.data as Record<string, unknown>;
  const weights = data['score_weights'] && typeof data['score_weights'] === 'object'
    ? data['score_weights'] as Record<string, unknown>
    : {};
  for (const field of fields) {
    const raw = field.key in weights ? weights[field.key] : data[field.key];
    values[field.key] = Array.isArray(raw) ? raw.join(', ') : String(raw ?? '');
    if (!values[field.key] && field.type === 'select' && field.options?.length) values[field.key] = field.options[0];
  }
  return values;
}

export function normalizeFormData(entryType: string, values: Record<string, string>): Record<string, unknown> {
  const data: Record<string, unknown> = { ...values };
  const listKeys = [
    'activation', 'allowed_task_kinds', 'blocked_ai_modes', 'allowed_ai_modes',
    'allowed_model_engines', 'tasks', 'labels', 'allowed_base_urls', 'allowed_engines',
  ];
  for (const key of listKeys) {
    if (key in data) data[key] = String(data[key] ?? '').split(',').map(item => item.trim()).filter(Boolean);
  }
  const booleanKeys = [
    'allow_hidden_states', 'allow_logits', 'allow_attention', 'allow_free_text_generation',
    'allow_tool_decision_from_model_text', 'allow_code_generation', 'require_controlled_write_policy',
    'enabled', 'fallback_to_deterministic', 'external_calls_allowed', 'diagnostics_enabled',
    'restricted_inference_rerank_enabled', 'trace_scores', 'fallback_without_model', 'allow_mock_fallback',
  ];
  for (const key of booleanKeys) if (key in data) data[key] = data[key] !== 'false';
  for (const key of ['max_input_chars', 'max_batch_size', 'priority', 'max_candidates', 'dimensions', 'timeout_seconds']) {
    if (key in data) {
      const parsed = Number.parseInt(String(data[key] ?? '0'), 10);
      data[key] = Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
    }
  }
  if ('weight' in data) {
    const parsed = Number.parseFloat(String(data['weight'] ?? '1'));
    data['weight'] = Number.isFinite(parsed) ? parsed : 1;
  }
  if (entryType === 'codecompass_ranking') {
    const scoreWeights: Record<string, number> = {};
    for (const key of ['embedding_score', 'graph_score', 'symbol_score', 'transformer_rerank_score', 'policy_penalty']) {
      const parsed = Number.parseFloat(String(data[key] ?? '0'));
      scoreWeights[key] = Number.isFinite(parsed) ? parsed : 0;
      delete data[key];
    }
    data['score_weights'] = scoreWeights;
  }
  return data;
}
