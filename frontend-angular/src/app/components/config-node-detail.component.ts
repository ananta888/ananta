import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  DestroyRef,
  EventEmitter,
  Input,
  Output,
  inject,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import { ConfigGraph, ConfigGraphNode, PatchOp } from '../models/config-graph.model';
import { ConfigGraphService } from '../services/config-graph.service';
import {
  CLONE_DEFS,
  type CloneFormState,
  type ConnectedNode,
} from './config-graph-editor.models';
import {
  behaviorDimensions,
  characterBadge,
  configFieldsFor,
  formValuesForNode,
  isCloneable,
  isEditableConfigNode,
  nodeTypeColor,
  normalizeFormData,
} from './config-node-detail.helpers';

export type CreatableConfigType =
  | 'agent_profile'
  | 'path_rule'
  | 'restricted_inference_model'
  | 'restricted_inference_task';

@Component({
  standalone: true,
  selector: 'app-config-node-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule],
  templateUrl: './config-node-detail.component.html',
  styleUrls: ['./config-graph-editor.component.scss'],
})
export class ConfigNodeDetailComponent {
  private readonly service = inject(ConfigGraphService);
  private readonly destroyRef = inject(DestroyRef);
  readonly cdr = inject(ChangeDetectorRef);

  @Input() node: ConfigGraphNode | null = null;
  @Input() connectedNodes: ConnectedNode[] = [];
  @Input() accentColor = '#4A90D9';

  @Output() closed = new EventEmitter<void>();
  @Output() nodeSelected = new EventEmitter<ConfigGraphNode>();
  @Output() showInGraph = new EventEmitter<ConfigGraphNode>();
  @Output() patchQueued = new EventEmitter<{ op: PatchOp; node: ConfigGraphNode }>();
  @Output() graphChanged = new EventEmitter<ConfigGraph>();
  @Output() editorActiveChange = new EventEmitter<boolean>();

  cloneState: CloneFormState | null = null;

  readonly behaviorDims = behaviorDimensions;
  readonly characterBadge = characterBadge;
  readonly configFieldsFor = configFieldsFor;
  readonly isCloneable = isCloneable;
  readonly isEditableConfigNode = isEditableConfigNode;
  readonly nodeTypeColor = nodeTypeColor;

  startCreate(entryType: CreatableConfigType): void {
    const fields = CLONE_DEFS[entryType] ?? [];
    const values: Record<string, string> = {};
    for (const field of fields) {
      values[field.key] = field.type === 'select' && field.options?.length ? field.options[0] : '';
    }
    this.openForm({ sourceNode: null, entryType, mode: 'create', fields, values, saving: false, error: null });
  }

  prefillPathRule(suggestion: { glob: string; blocked: string }): void {
    this.startCreate('path_rule');
    if (!this.cloneState) return;
    this.cloneState.values['path_glob'] = suggestion.glob;
    this.cloneState.values['blocked_ai_modes'] = suggestion.blocked;
  }

  startClone(source: ConfigGraphNode): void {
    const entryType = source.node_type as CreatableConfigType;
    const fields = CLONE_DEFS[entryType] ?? [];
    const values = formValuesForNode(source, fields);
    if (entryType === 'agent_profile') values['profile_id'] = '';
    if (entryType === 'path_rule') values['path_glob'] = '';
    if (entryType === 'restricted_inference_model' || entryType === 'restricted_inference_task') values['id'] = '';
    this.openForm({ sourceNode: source, entryType, mode: 'clone', fields, values, saving: false, error: null });
  }

  startEdit(source: ConfigGraphNode): void {
    const entryType = source.node_type as CreatableConfigType;
    const fields = CLONE_DEFS[entryType] ?? [];
    this.openForm({
      sourceNode: source,
      entryType,
      mode: 'edit',
      fields,
      values: formValuesForNode(source, fields),
      saving: false,
      error: null,
    });
  }

  save(): void {
    if (!this.cloneState) return;
    const { entryType, values, sourceNode, mode } = this.cloneState;
    const data = normalizeFormData(entryType, values);
    if (mode === 'edit' && sourceNode) {
      const node = { ...sourceNode, data: { ...sourceNode.data, ...data } };
      this.patchQueued.emit({ op: { op: 'set_data', target: sourceNode.id, data }, node });
      this.closeForm();
      return;
    }
    this.cloneState.saving = true;
    this.cloneState.error = null;
    this.cdr.markForCheck();
    this.service.createConfigEntry(entryType, data).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: graph => {
        this.graphChanged.emit(graph);
        this.closeForm();
      },
      error: error => {
        if (this.cloneState) {
          this.cloneState.error = error?.error?.error ?? 'Speichern fehlgeschlagen';
          this.cloneState.saving = false;
        }
        this.cdr.markForCheck();
      },
    });
  }

  cancel(): void {
    this.closeForm();
  }

  private openForm(state: CloneFormState): void {
    this.cloneState = state;
    this.editorActiveChange.emit(true);
    this.cdr.markForCheck();
  }

  private closeForm(): void {
    this.cloneState = null;
    this.editorActiveChange.emit(false);
    this.cdr.markForCheck();
  }
}
