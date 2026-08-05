import {
  GraphEdgeType,
  GraphNodeKind,
  SEMANTIC_TRANSLATION_EDGE_TYPES,
  SEMANTIC_TRANSLATION_NODE_KINDS,
} from './graph.model';

export type GraphFilterMode = 'all' | 'none' | 'subset';

/**
 * Explicit selection state. Empty arrays no longer have two implicit meanings,
 * and hidden inventory entries remain selectable from legends and toolbars.
 */
export interface GraphFilterSelection<T extends string = string> {
  readonly mode: GraphFilterMode;
  readonly values: readonly T[];
}

export interface GraphFilter {
  readonly searchText: string;
  readonly nodeKinds: GraphFilterSelection<string>;
  readonly edgeTypes: GraphFilterSelection<string>;
  readonly domains: GraphFilterSelection<string>;
}

export const ALL_SELECTION: GraphFilterSelection<string> = Object.freeze({
  mode: 'all',
  values: Object.freeze([] as string[]),
});
export const NONE_SELECTION: GraphFilterSelection<string> = Object.freeze({
  mode: 'none',
  values: Object.freeze([] as string[]),
});

export const EMPTY_FILTER: GraphFilter = Object.freeze({
  searchText: '',
  nodeKinds: ALL_SELECTION,
  edgeTypes: ALL_SELECTION,
  domains: ALL_SELECTION,
});

export function graphSelectionContains(selection: GraphFilterSelection<string>, value: string): boolean {
  if (selection.mode === 'all') return true;
  if (selection.mode === 'none') return false;
  return selection.values.includes(value);
}

export function graphSelectionFromVisible(
  visibleValues: Iterable<string>,
  inventory: readonly string[],
): GraphFilterSelection<string> {
  const inventorySet = new Set(inventory);
  const values = [...new Set(visibleValues)]
    .filter(value => inventorySet.has(value))
    .sort((left, right) => left.localeCompare(right));
  if (values.length === 0) return NONE_SELECTION;
  if (values.length === inventorySet.size) return ALL_SELECTION;
  return Object.freeze({ mode: 'subset', values: Object.freeze(values) });
}

export function graphSelectionToggle(
  current: GraphFilterSelection<string>,
  value: string,
  visible: boolean,
  inventory: readonly string[],
): GraphFilterSelection<string> {
  const selected = new Set(
    current.mode === 'all' ? inventory : current.mode === 'none' ? [] : current.values,
  );
  if (visible) selected.add(value);
  else selected.delete(value);
  return graphSelectionFromVisible(selected, inventory.includes(value) ? inventory : [...inventory, value]);
}

export const ALL_NODE_KINDS: GraphNodeKind[] = [
  'python_class',
  'python_function',
  'python_method',
  'python_module_summary',
  'python_file',
  'python_import',
  'typescript_class',
  'typescript_function',
  'typescript_method',
  'typescript_interface',
  'typescript_type',
  'typescript_const',
  'typescript_enum',
  'typescript_folder_summary',
  'typescript_file',
  'typescript_constructor',
  'typescript_import',
  'java_method',
  'java_type',
  'java_file',
  'java_constructor',
  'java_constructor_detail',
  'java_method_detail',
  'java_module_summary',
  'md_file',
  'md_section',
  'xml_tag',
  'xml_file',
  'xml_node_detail',
  'yaml_file',
  'yaml_entry',
  'properties_file',
  'properties_entry',
  'config',
  'wiki_article',
  'wiki_section',
  'wiki_chunk',
  'package_manager',
  'external_package',
  'buildable_component',
  'repository',
  'directory',
  'source_file',
  ...SEMANTIC_TRANSLATION_NODE_KINDS,
  'aggregator',
  'runner',
  'test',
  'unknown',
];

export const ALL_EDGE_TYPES: GraphEdgeType[] = [
  'bean_factory_method',
  'calls_probable_target',
  'child_of_type',
  'child_of_file',
  'contains_directory',
  'contains_entry',
  'contains_file',
  'contains_method',
  'contains_section',
  'contains_symbol',
  'contains_type',
  'controller_endpoint_declares',
  'declares_constructor',
  'declares_method',
  'declares_bean',
  'field_type_uses',
  'frontend_guard_refs_field',
  'generic_type_uses',
  'imports_module',
  'imports_symbol',
  'injects_dependency',
  'transactional_boundary',
  'jpa_relation',
  'mapper_maps_type',
  'method_param_type_uses',
  'method_return_type_uses',
  'parent_child',
  'permission_checks_field',
  'policy_applies_to_field',
  'uses_type',
  'related',
  'wiki_link',
  'depends_on',
  'aggregates',
  'built_by',
  'tested_by',
  'test_calls_endpoint',
  'test_targets_type',
  'runs',
  'covers',
  'service_uses_repository',
  ...SEMANTIC_TRANSLATION_EDGE_TYPES,
];
