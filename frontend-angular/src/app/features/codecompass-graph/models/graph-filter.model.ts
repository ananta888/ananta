import { GraphNodeKind, GraphEdgeType } from './graph.model';

export interface GraphFilter {
  searchText: string;
  nodeKindFilter: GraphNodeKind[];
  edgeTypeFilter: GraphEdgeType[];
}

export const EMPTY_FILTER: GraphFilter = {
  searchText: '',
  nodeKindFilter: [],
  edgeTypeFilter: [],
};

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
  'unknown',
];

export const ALL_EDGE_TYPES: GraphEdgeType[] = [
  'calls_probable_target',
  'injects_dependency',
  'field_type_uses',
  'extends',
  'implements',
  'child_of_type',
  'child_of_file',
  'declares_method',
  'declares_bean',
  'transactional_boundary',
  'jpa_relation',
  'parent_child',
  'related',
];
