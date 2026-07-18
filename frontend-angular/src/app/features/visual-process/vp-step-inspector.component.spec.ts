import { ɵresolveComponentResources, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { NodeDefinitionContract, TaskKindInfo, VpGraph, VpRuntimeOverlay } from './visual-process-api.service';
import { VpNodeDefinitionRegistryService } from './vp-node-definition-registry.service';
import { VpStepInspectorComponent } from './vp-step-inspector.component';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(new URL(resource, import.meta.url), 'utf8'));
});

function graph(): VpGraph {
  return {
    id: 'vp-1', name: 'Training', description: '', version: '1', tags: [], edges: [],
    steps: [{
      id: 'train', label: 'LoRA trainieren', kind: 'ml_intern_train_lora', gate: true,
      io: { inputs: [], outputs: [] }, position: { x: 0, y: 0 }, policy_hints: [],
      metadata: {
        dataset_id: 'dataset-1', training_profile_id: 'generic-safe', base_model: 'model-1',
        dataset_path: 'legacy/train.jsonl', dataset_root: '/legacy/root', output_dir: 'legacy-output',
      },
    }],
  };
}

function buildGraph(): VpGraph {
  return {
    id: 'vp-build', name: 'Dataset Build', description: '', version: '1', tags: [], edges: [],
    steps: [{
      id: 'build', label: 'Dataset katalogisieren', kind: 'ml_intern_build_lora_dataset', gate: false,
      io: { inputs: [], outputs: [] }, position: { x: 0, y: 0 }, policy_hints: [],
      metadata: {
        name: 'Kuratierte VP-Daten', dataset_id: 'dataset-1',
        dataset_root: '/legacy/root', source_paths: ['legacy/train.jsonl'], output_path: 'legacy-output.jsonl',
      },
    }],
  };
}

describe('VpStepInspectorComponent LoRA integration', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [VpStepInspectorComponent] }).compileComponents();
  });

  function create(runtimeOverlay: VpRuntimeOverlay | null = null, value: VpGraph = graph()) {
    const fixture = TestBed.createComponent(VpStepInspectorComponent);
    const component = fixture.componentInstance;
    component.graph = signal(value);
    component.selectedId = signal(value.steps[0].id);
    const taskKinds: TaskKindInfo[] = [
      {
        id: 'ml_intern_train_lora', label: 'LoRA', group: 'ml', dispatch_capable: false,
        description: '', implementation_status: 'production', implementation_state: 'wired_and_executable',
      },
      {
        id: 'ml_intern_build_lora_dataset', label: 'LoRA Dataset', group: 'ml', dispatch_capable: false,
        description: '', implementation_status: 'production', implementation_state: 'wired_and_executable',
      },
    ];
    component.taskKindList = signal(taskKinds);
    component.skillProfiles = signal([]);
    component.modelProfiles = signal([]);
    component.fallbackGroups = signal({});
    component.trainingDatasets = [{
      id: 'dataset-1', name: 'Kuratierte Daten', format: 'instruction', status: 'valid', size_bytes: 10,
      record_count: 12, train_record_count: 10, validation_record_count: 2, trainable: true,
    }];
    component.trainingProfiles = [{ id: 'generic-safe', label: 'Generic safe', available: true }];
    component.trainingBaseModels = [{ id: 'model-1', label: 'Local model', local: true, available: true, compatible_backends: ['mock'] }];
    const registry = new VpNodeDefinitionRegistryService();
    component.nodeDefinitions = taskKinds.map(kind => registry.definition(kind));
    component.artifactKinds = [];
    component.edgeKinds = [];
    component.encodingModes = [];
    component.ragChannels = [];
    component.runtimeOverlay = runtimeOverlay;
    fixture.detectChanges();
    fixture.detectChanges();
    return fixture;
  }

  it('uses catalog selects and exposes legacy path fields only as deprecated values', () => {
    const fixture = create();
    const element = fixture.nativeElement as HTMLElement;

    const datasetSelect = element.querySelector('[data-field-path="/metadata/dataset_id"] select') as HTMLSelectElement;
    const profileSelect = element.querySelector('[data-field-path="/metadata/training_profile_id"] select') as HTMLSelectElement;
    expect(Array.from(datasetSelect.options).some(option => option.textContent?.includes('dataset-1'))).toBe(true);
    expect(Array.from(profileSelect.options).some(option => option.textContent?.includes('generic-safe'))).toBe(true);
    expect(element.textContent).toContain('Deprecated:');
    expect(element.textContent).toContain('dataset_path');
    expect(element.textContent).toContain('legacy/train.jsonl');
    expect(element.textContent).toContain('Training-Control-Center');
    expect(element.querySelector('input[placeholder*="train.jsonl"]')).toBeNull();
  });

  it('renders the runtime job ID, phase, terminal result and Control-Center links', () => {
    const overlay: VpRuntimeOverlay = {
      run_id: 'run-1', workflow_id: 'wf-1', overall_status: 'done', current_step_ids: [], updated_at: 1,
      steps: {
        train: {
          step_id: 'train', status: 'succeeded', training: {
            jobId: 'job-17', datasetId: 'dataset-1', status: 'completed', phase: 'publishing',
            trainingProfileId: 'generic-safe', terminal: true, terminalResult: { adapter_id: 'adapter-1' },
            modelTrainingUrl: '/model-training', jobUrl: '/model-training?tab=jobs&job_id=job-17',
            datasetUrl: '/model-training?tab=datasets&dataset_id=dataset-1',
          },
        },
      },
    };
    const element = create(overlay).nativeElement as HTMLElement;

    expect(element.textContent).toContain('job-17');
    expect(element.textContent).toContain('publishing');
    expect(element.textContent).toContain('adapter-1');
    expect(element.querySelector('a[href="/model-training?tab=jobs&job_id=job-17"]')).not.toBeNull();
  });

  it('uses catalog selection for dataset builds and renders legacy path fields read-only', () => {
    const element = create(null, buildGraph()).nativeElement as HTMLElement;

    const datasetSelect = element.querySelector('[data-field-path="/metadata/dataset_id"] select') as HTMLSelectElement;
    expect(Array.from(datasetSelect.options).some(option => option.textContent?.includes('dataset-1'))).toBe(true);
    expect(element.textContent).toContain('bounded Records');
    expect(element.textContent).toContain('Deprecated und nur lesbar:');
    expect(element.textContent).toContain('/legacy/root');
    expect(element.textContent).toContain('legacy/train.jsonl');
    expect(element.textContent).toContain('legacy-output.jsonl');
    expect(element.textContent).not.toContain('Dataset-Root');
    expect(element.textContent).not.toContain('Quell-Dateien');
    expect(element.textContent).not.toContain('Output JSONL');
    expect(element.querySelector('input[placeholder*="train.jsonl"]')).toBeNull();
  });

  it('renders the canonical dataset-build result and Control-Center link', () => {
    const overlay: VpRuntimeOverlay = {
      run_id: 'run-build', workflow_id: 'wf-build', overall_status: 'done', current_step_ids: [], updated_at: 1,
      steps: {
        build: {
          step_id: 'build', status: 'succeeded', datasetBuild: {
            datasetId: 'dataset-1', status: 'validated', validationStatus: 'passed', trainable: true,
            recordCount: 12, trainRecordCount: 10, validationRecordCount: 2,
            sourceMode: 'bounded_upstream_records', modelTrainingUrl: '/model-training',
            datasetUrl: '/model-training?tab=datasets&dataset_id=dataset-1',
          },
        },
      },
    };
    const element = create(overlay, buildGraph()).nativeElement as HTMLElement;

    expect(element.querySelector('[data-testid="vp-dataset-build-runtime"]')?.textContent).toContain('dataset-1');
    expect(element.querySelector('[data-testid="vp-dataset-build-runtime"]')?.textContent).toContain('10 / 2');
    expect(element.querySelector('a[href="/model-training?tab=datasets&dataset_id=dataset-1"]')).not.toBeNull();
  });

  it('renders a new kind exclusively from its registry field contract and writes through the command port', () => {
    const definitionContract: NodeDefinitionContract = {
      contract_version: 'ananta.visual_process.node_definition.v1', registry_version: 'test-v1',
      kind: 'fixture_kind', label: 'Fixture', category: 'test', purpose: 'Renderer contract fixture',
      runtime: { implementation_state: 'wired_and_executable', implementation_status: 'test_only' },
      execution: { visual_process_executable: true }, inputs: [], outputs: [], defaults: { metadata: { count: 2, rows: [] } },
      fields: [
        { path: '/label', label: 'Label', field_type: 'text', help_text: 'Name', required: true },
        { path: '/metadata/count', label: 'Anzahl', field_type: 'number', help_text: 'Wirkung', constraints: { minimum: 1, maximum: 5, integer: true } },
        { path: '/metadata/rows', label: 'Zeilen', field_type: 'structured_list', help_text: 'Strukturierte Werte', default: [] },
        { path: '/io/inputs', label: 'Inputs', field_type: 'io_port', help_text: 'Eingänge', default: [] },
        {
          path: '/metadata/advanced', label: 'Erweiterter Wert', field_type: 'text', help_text: 'Kurzbeschreibung',
          default: 'standard', example: 'beispiel', effect: 'Ändert das Fixture-Verhalten.', essential: false,
          visible_when: { path: '/metadata/count', equals: 4 }, required_when: { path: '/metadata/count', equals_any: [4] },
        },
      ],
    };
    const value: VpGraph = {
      id: 'fixture', name: 'Fixture', description: '', version: '1', tags: [], edges: [],
      steps: [{ id: 'fixture-step', label: 'Fixture', kind: 'fixture_kind', gate: false, policy_hints: [], io: { inputs: [], outputs: [] }, position: { x: 0, y: 0 }, metadata: { count: 2, rows: [] } }],
    };
    const fixture = TestBed.createComponent(VpStepInspectorComponent);
    const component = fixture.componentInstance;
    component.graph = signal(value); component.selectedId = signal('fixture-step');
    component.taskKindList = signal([{ id: 'fixture_kind', label: 'Fixture', group: 'test', description: '', dispatch_capable: true }]);
    component.skillProfiles = signal([]); component.modelProfiles = signal([]); component.fallbackGroups = signal({});
    component.artifactKinds = ['text']; component.edgeKinds = []; component.encodingModes = []; component.ragChannels = [];
    component.nodeDefinitions = [new VpNodeDefinitionRegistryService().fromBackendDefinition(definitionContract)];
    const commandLabels: string[] = [];
    component.graphMutator = (label, mutator) => {
      commandLabels.push(label);
      component.graph.update(current => { const copy = structuredClone(current); mutator(copy); return copy; });
    };
    fixture.detectChanges();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('.vpe-structured-input')).not.toBeNull();
    expect(element.textContent).toContain('Port hinzufügen');
    expect(element.textContent).not.toContain('Erweiterter Wert');
    const count = component.definitionFields().find(field => field.path === '/metadata/count')!;
    component.setFieldValue(count, 4);
    expect(component.graph().steps[0].metadata?.['count']).toBe(4);
    expect(commandLabels).toContain('Anzahl ändern');
    component.expertMode.set(true);
    fixture.detectChanges();
    expect(element.textContent).toContain('Erweiterter Wert *');
    expect(element.textContent).toContain('Kurzbeschreibung');
    expect(element.textContent).toContain('Wirkung: Ändert das Fixture-Verhalten.');
    expect(element.textContent).toContain('Standard: standard');
    expect(element.textContent).toContain('Beispiel: beispiel');
  });

  it('keeps a kind without registry definition read-only and lossless', () => {
    const value: VpGraph = {
      id: 'unknown', name: 'Unknown', description: '', version: '1', tags: [], edges: [],
      steps: [{ id: 'future', label: 'Future', kind: 'future_kind', gate: false, policy_hints: [], io: { inputs: [], outputs: [] }, position: { x: 0, y: 0 }, metadata: { future_value: { preserved: true }, api_key: 'must-not-render' } }],
    };
    const fixture = create(null, value);
    fixture.componentInstance.nodeDefinitions = [];
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.textContent).toContain('nur lesbar');
    expect(element.textContent).toContain('future_value');
    expect(element.textContent).not.toContain('must-not-render');
    expect(element.textContent).toContain('[REDACTED]');
    expect(element.querySelector('select')).toBeNull();
    expect(fixture.componentInstance.graph().steps[0].metadata?.['future_value']).toEqual({ preserved: true });
  });

  it('reads the legacy reranker alias but writes only the canonical metadata.weight field', () => {
    const value: VpGraph = {
      id: 'rerank', name: 'Rerank', description: '', version: '1', tags: [], edges: [],
      steps: [{ id: 'rerank-step', label: 'Rerank', kind: 'rerank', gate: false, policy_hints: [], io: { inputs: [], outputs: [] }, position: { x: 0, y: 0 }, metadata: { reranker_weight: 0.27 } }],
    };
    const fixture = create(null, value);
    const component = fixture.componentInstance;
    component.nodeDefinitions = [new VpNodeDefinitionRegistryService().find('rerank', component.taskKindList())];
    component.graphMutator = (_label, mutator) => {
      component.graph.update(current => { const copy = structuredClone(current); mutator(copy); return copy; });
    };

    const weight = component.definitionFields().find(field => field.path === '/metadata/weight')!;
    expect(component.fieldValue(weight)).toBe(0.27);
    component.setFieldValue(weight, 0.6);
    expect(component.graph().steps[0].metadata?.['weight']).toBe(0.6);
    expect(component.graph().steps[0].metadata?.['reranker_weight']).toBe(0.27);
    expect(component.fieldValue(weight)).toBe(0.6);
  });

  it('contains no direct node-kind branch in the inspector shell', async () => {
    const template = await readFile('src/app/features/visual-process/vp-step-inspector.component.html', 'utf8');
    expect(template).not.toMatch(/(?:selectedStep\(\)!|step)\.kind\s*===/);
    expect(template).not.toContain('@switch (field.type)');
  });
});
