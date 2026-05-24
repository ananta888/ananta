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
  'java_method',
  'java_type',
  'config',
  'xml_tag',
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
  'related',
];
