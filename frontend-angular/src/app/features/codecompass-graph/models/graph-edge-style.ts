import { GraphEdgeType } from './graph.model';

const EDGE_COLORS: Partial<Record<GraphEdgeType, string>> = {
  parent_child: '#64748b',
  contains_symbol: '#64748b',
  contains_method: '#64748b',
  contains_entry: '#64748b',
  contains_section: '#64748b',
  contains_type: '#64748b',
  child_of_type: '#94a3b8',
  child_of_file: '#94a3b8',
  declares_method: '#2563eb',
  declares_constructor: '#2563eb',
  declares_bean: '#2563eb',
  controller_endpoint_declares: '#2563eb',
  bean_factory_method: '#2563eb',
  imports_module: '#0891b2',
  imports_symbol: '#06b6d4',
  calls_probable_target: '#dc2626',
  uses_type: '#7c3aed',
  field_type_uses: '#7c3aed',
  generic_type_uses: '#7c3aed',
  method_param_type_uses: '#7c3aed',
  method_return_type_uses: '#7c3aed',
  returns: '#9333ea',
  extends: '#ea580c',
  implements: '#f97316',
  injects_dependency: '#16a34a',
  transactional_boundary: '#ca8a04',
  jpa_relation: '#be123c',
  related: '#94a3b8',
};

export function graphEdgeColor(edgeType: GraphEdgeType | string): string {
  return EDGE_COLORS[edgeType as GraphEdgeType] ?? '#94a3b8';
}
