import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { ConfigGraphEditorComponent } from './config-graph-editor.component';
import { ConfigGraphService } from '../services/config-graph.service';
import { ConfigGraph } from '../models/config-graph.model';

const GRAPH: ConfigGraph = {
  schema: 'ananta_configuration_graph.v1',
  snapshot_id: 'test-snapshot',
  generated_at: 0,
  node_count: 3,
  edge_count: 2,
  diagnostics: [],
  views: {
    configuration_overview_view: ['surface::chat', 'agent_profile::coder', 'path_rule::tests'],
    profile_activation_view: ['surface::chat', 'agent_profile::coder'],
    policy_path_view: ['path_rule::tests'],
    planning_flow_view: [],
    agent_runtime_view: [],
    context_pipeline_view: [],
    effective_config_view: ['agent_profile::coder'],
  },
  nodes: {
    'surface::chat': {
      id: 'surface::chat',
      node_type: 'surface',
      label: 'AI Snake Chat',
      source_file: null,
      source_line: null,
      source_kind: null,
      source_pointer: null,
      writable: false,
      runtime_source: 'default',
      runtime_active: true,
      stale: false,
      effective_value: null,
      declared_value: null,
      data: { surface: 'ai_snake_chat' },
      diagnostics: [],
    },
    'agent_profile::coder': {
      id: 'agent_profile::coder',
      node_type: 'agent_profile',
      label: 'Coder',
      source_file: 'docs/agent-profiles/profile-map.json',
      source_line: null,
      source_kind: 'profile_map',
      source_pointer: '/profiles/coder',
      writable: true,
      runtime_source: 'profile-map',
      runtime_active: true,
      stale: false,
      effective_value: null,
      declared_value: null,
      data: { profile_id: 'coder', primary_role: 'code_writer', allowed_task_kinds: ['coding'] },
      diagnostics: [],
    },
    'path_rule::tests': {
      id: 'path_rule::tests',
      node_type: 'path_rule',
      label: 'tests/**',
      source_file: 'user.json',
      source_line: null,
      source_kind: 'user_config',
      source_pointer: '/path_ai_modes/0',
      writable: true,
      runtime_source: 'user-config',
      runtime_active: false,
      stale: false,
      effective_value: null,
      declared_value: null,
      data: { path_glob: 'tests/**', blocked_ai_modes: ['full_llm'] },
      diagnostics: ['disabled for test'],
    },
  },
  edges: [
    {
      source: 'surface::chat',
      target: 'agent_profile::coder',
      edge_type: 'activates',
      priority: 0,
      condition: null,
      policy_effect: null,
      source_ref: null,
    },
    {
      source: 'path_rule::tests',
      target: 'agent_profile::coder',
      edge_type: 'blocked_by_policy',
      priority: 0,
      condition: null,
      policy_effect: 'block',
      source_ref: null,
    },
  ],
};

describe('ConfigGraphEditorComponent', () => {
  let fixture: ComponentFixture<ConfigGraphEditorComponent>;
  let component: ConfigGraphEditorComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ConfigGraphEditorComponent],
      providers: [
        {
          provide: ConfigGraphService,
          useValue: {
            getGraph: () => of(GRAPH),
          },
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ConfigGraphEditorComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('starts with the complete configuration overview', () => {
    expect(component.activeView).toBe('configuration_overview_view');
    expect(component.configPanelItems.map(item => item.id)).toEqual([
      'surface::chat',
      'agent_profile::coder',
      'path_rule::tests',
    ]);
  });

  it('filters overview nodes by type, status, and search text', () => {
    component.graphNodeType = 'path_rule';
    component.graphStatus = 'inactive';
    component.graphSearchText = 'full_llm';
    component.onGraphFilterChanged();

    expect(component.visibleNodeIds).toEqual(['path_rule::tests']);
    expect(component.configPanelItems[0].label).toBe('tests/**');
  });

  it('keeps all view nodes in config mode instead of only primary types', () => {
    component.setView('profile_activation_view');

    expect(component.configPanelItems.map(item => item.node_type)).toEqual([
      'surface',
      'agent_profile',
    ]);
  });
});
