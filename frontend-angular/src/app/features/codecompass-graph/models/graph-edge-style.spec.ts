import { graphEdgeColor } from './graph-edge-style';

describe('graphEdgeColor', () => {
  it('uses distinct colors for common semantic edge groups', () => {
    expect(graphEdgeColor('imports_symbol')).not.toBe(graphEdgeColor('parent_child'));
    expect(graphEdgeColor('calls_probable_target')).not.toBe(graphEdgeColor('imports_symbol'));
    expect(graphEdgeColor('extends')).not.toBe(graphEdgeColor('field_type_uses'));
  });

  it('falls back to the neutral edge color for unknown relations', () => {
    expect(graphEdgeColor('not_indexed_yet')).toBe('#94a3b8');
  });

  it('styles repository hierarchy relations as known semantic links', () => {
    expect(graphEdgeColor('contains_directory')).not.toBe('#94a3b8');
    expect(graphEdgeColor('contains_file')).not.toBe('#94a3b8');
  });

  it('styles semantic-translation relations without the unknown fallback', () => {
    for (const relation of ['declares', 'calls', 'reads', 'writes', 'verified_by']) {
      expect(graphEdgeColor(relation)).not.toBe('#94a3b8');
    }
  });
});
