import { Inject, Injectable, InjectionToken, Optional, computed, signal } from '@angular/core';

import { GraphSaveResult, ValidationResult, VpGraph } from './visual-process-api.service';
import { emptyGraph } from './vp-editor-config';
import { CanvasHitTarget } from './vp-editor-context.models';
import {
  VpEditorCommand,
  VpEditorSaveAcceptance,
  VpEditorSaveRequest,
  VpEditorStatePort,
  VpGraphMutationOptions,
} from './vp-editor-state.port';

export type {
  VpEditorCommand,
  VpEditorSaveAcceptance,
  VpEditorSaveRequest,
  VpGraphMutationOptions,
} from './vp-editor-state.port';

interface VpHistoryEntry {
  before: VpGraph;
  after: VpGraph;
  label: string;
  coalesceKey?: string;
}

interface VpOpenTransaction {
  before: VpGraph;
  label: string;
}

interface VpPersistenceIdentity {
  version: string;
  graphSchemaVersion?: string;
  nodeRegistryVersion?: string;
  definitionRevision?: number;
  baseGraphHash?: string;
}

export const VP_EDITOR_HISTORY_LIMIT = new InjectionToken<number>('VP_EDITOR_HISTORY_LIMIT', {
  factory: () => 100,
});

function cloneGraph(graph: VpGraph): VpGraph {
  return structuredClone(graph);
}

function graphFingerprint(graph: VpGraph): string {
  return JSON.stringify(graph);
}

/**
 * Instance-scoped source of truth for editor mutations.
 *
 * Components never need to coordinate dirty-state, validation invalidation or
 * undo/redo independently. A drag may update the graph many times while one
 * open transaction records a single history entry.
 */
@Injectable()
export class VpEditorStateFacade implements VpEditorStatePort {
  readonly graph = signal<VpGraph>(emptyGraph());
  readonly selectedId = signal<string | null>(null);
  readonly dirty = signal(false);
  readonly validation = signal<ValidationResult | null>(null);
  readonly edgeMode = signal(false);
  readonly edgeSourceId = signal<string | null>(null);
  readonly revision = signal(0);
  readonly hoverTarget = signal<CanvasHitTarget | null>(null);
  readonly focusedTarget = signal<CanvasHitTarget | null>(null);
  readonly conversationTarget = signal<CanvasHitTarget | null>(null);

  private readonly undoEntries = signal<VpHistoryEntry[]>([]);
  private readonly redoEntries = signal<VpHistoryEntry[]>([]);
  private savedFingerprint = graphFingerprint(this.graph());
  private transaction: VpOpenTransaction | null = null;
  private readonly historyLimit: number;
  private persistenceIdentity = this.capturePersistenceIdentity(this.graph());
  private stateEpoch = 0;
  private latestSaveRequestId = 0;

  // Explicit constructor injection also keeps the state aggregate usable in
  // framework-free command/history tests.
  // eslint-disable-next-line @angular-eslint/prefer-inject
  constructor(@Optional() @Inject(VP_EDITOR_HISTORY_LIMIT) historyLimit: number | null = null) {
    this.historyLimit = Math.max(1, Math.floor(historyLimit ?? 100));
  }

  readonly canUndo = computed(() => this.undoEntries().length > 0);
  readonly canRedo = computed(() => this.redoEntries().length > 0);
  readonly undoLabel = computed(() => this.undoEntries().at(-1)?.label ?? '');
  readonly redoLabel = computed(() => this.redoEntries().at(-1)?.label ?? '');

  initialize(graph: VpGraph): void {
    const next = cloneGraph(graph);
    this.stateEpoch += 1;
    this.persistenceIdentity = this.capturePersistenceIdentity(next);
    this.graph.set(next);
    this.savedFingerprint = graphFingerprint(next);
    this.selectedId.set(null);
    this.dirty.set(false);
    this.validation.set(null);
    this.edgeMode.set(false);
    this.edgeSourceId.set(null);
    this.hoverTarget.set(null);
    this.focusedTarget.set(null);
    this.conversationTarget.set(null);
    this.undoEntries.set([]);
    this.redoEntries.set([]);
    this.transaction = null;
    this.revision.update(value => value + 1);
  }

  replaceGraph(
    graph: VpGraph,
    options: { markDirty?: boolean; validation?: ValidationResult | null; resetHistory?: boolean } = {},
  ): void {
    const before = cloneGraph(this.graph());
    const after = cloneGraph(graph);
    this.stateEpoch += 1;
    this.persistenceIdentity = this.capturePersistenceIdentity(after);
    this.graph.set(after);
    if (options.resetHistory !== false) {
      this.undoEntries.set([]);
      this.redoEntries.set([]);
      this.transaction = null;
    } else if (graphFingerprint(before) !== graphFingerprint(after)) {
      this.pushHistory({ before, after: cloneGraph(after), label: 'Graph ersetzen' });
    }
    if (!options.markDirty) this.savedFingerprint = graphFingerprint(after);
    this.validation.set(options.validation ?? null);
    this.dirty.set(options.markDirty === true);
    this.selectedId.set(null);
    this.revision.update(value => value + 1);
  }

  execute(
    label: string,
    reducer: (graph: VpGraph) => VpGraph,
    options: VpGraphMutationOptions = {},
  ): boolean {
    return this.dispatch({ label, apply: reducer, coalesceKey: options.coalesceKey }, options);
  }

  dispatch(command: VpEditorCommand, options: VpGraphMutationOptions = {}): boolean {
    const before = cloneGraph(this.graph());
    const after = cloneGraph(command.apply(cloneGraph(before)));
    if (graphFingerprint(before) === graphFingerprint(after)) return false;

    this.graph.set(after);
    if (options.recordHistory !== false && !this.transaction) {
      this.pushHistory({
        before,
        after: cloneGraph(after),
        label: command.label,
        coalesceKey: command.coalesceKey ?? options.coalesceKey,
      });
    }
    this.afterMutation(options);
    return true;
  }

  mutate(
    label: string,
    mutator: (draft: VpGraph) => void,
    options: VpGraphMutationOptions = {},
  ): boolean {
    return this.execute(label, graph => {
      mutator(graph);
      return graph;
    }, options);
  }

  beginTransaction(label: string): void {
    if (this.transaction) this.commitTransaction();
    this.transaction = { before: cloneGraph(this.graph()), label };
  }

  commitTransaction(): boolean {
    const open = this.transaction;
    this.transaction = null;
    if (!open) return false;
    const after = cloneGraph(this.graph());
    if (graphFingerprint(open.before) === graphFingerprint(after)) return false;
    this.pushHistory({ before: open.before, after, label: open.label });
    this.refreshDirty();
    return true;
  }

  cancelTransaction(): void {
    const open = this.transaction;
    this.transaction = null;
    if (!open) return;
    this.graph.set(this.withCurrentPersistenceIdentity(open.before));
    this.validation.set(null);
    this.refreshDirty();
    this.revision.update(value => value + 1);
  }

  undo(): boolean {
    this.commitTransaction();
    const entries = this.undoEntries();
    const entry = entries.at(-1);
    if (!entry) return false;
    this.undoEntries.set(entries.slice(0, -1));
    this.redoEntries.set([...this.redoEntries(), entry]);
    this.graph.set(this.withCurrentPersistenceIdentity(entry.before));
    this.validation.set(null);
    this.refreshDirty();
    this.revision.update(value => value + 1);
    return true;
  }

  redo(): boolean {
    const entries = this.redoEntries();
    const entry = entries.at(-1);
    if (!entry) return false;
    this.redoEntries.set(entries.slice(0, -1));
    this.undoEntries.set([...this.undoEntries(), entry].slice(-this.historyLimit));
    this.graph.set(this.withCurrentPersistenceIdentity(entry.after));
    this.validation.set(null);
    this.refreshDirty();
    this.revision.update(value => value + 1);
    return true;
  }

  markSaved(): void {
    this.persistenceIdentity = this.capturePersistenceIdentity(this.graph());
    this.savedFingerprint = graphFingerprint(this.graph());
    this.dirty.set(false);
  }

  captureSaveRequest(): VpEditorSaveRequest {
    const requestId = ++this.latestSaveRequestId;
    return Object.freeze({
      graph: cloneGraph(this.graph()),
      revision: this.revision(),
      state_epoch: this.stateEpoch,
      request_id: requestId,
    });
  }

  /**
   * Applies Hub persistence identity without overwriting edits made while the
   * request was in flight. The accepted request graph becomes the saved
   * baseline; a later local revision therefore remains dirty.
   */
  acceptSaveResult(
    result: GraphSaveResult,
    request: VpEditorSaveRequest,
  ): VpEditorSaveAcceptance {
    const current = this.graph();
    if (request.state_epoch !== this.stateEpoch) {
      return { status: 'rejected_stale', request_id: request.request_id };
    }
    if (current.id !== request.graph.id || result.id !== request.graph.id) {
      return { status: 'rejected_identity', request_id: request.request_id };
    }
    if (request.request_id !== this.latestSaveRequestId) {
      return { status: 'rejected_stale', request_id: request.request_id };
    }

    const persisted = this.withSaveResult(request.graph, result);
    const unchanged = this.revision() === request.revision
      && graphFingerprint(current) === graphFingerprint(request.graph);
    const next = this.withSaveResult(current, result);

    this.persistenceIdentity = this.capturePersistenceIdentity(next);
    this.savedFingerprint = graphFingerprint(persisted);
    this.graph.set(next);
    this.refreshDirty();
    this.revision.update(value => value + 1);
    return {
      status: unchanged && !this.dirty() ? 'accepted_clean' : 'accepted_dirty',
      request_id: request.request_id,
    };
  }

  previewTarget(target: CanvasHitTarget | null): void {
    this.hoverTarget.set(target);
  }

  focusTarget(target: CanvasHitTarget | null): void {
    this.focusedTarget.set(target);
  }

  freezeConversationTarget(target?: CanvasHitTarget | null): void {
    this.conversationTarget.set(target ?? this.focusedTarget() ?? this.hoverTarget());
  }

  clearConversationTarget(): void {
    this.conversationTarget.set(null);
  }

  destroy(): void {
    this.selectedId.set(null);
    this.edgeMode.set(false);
    this.edgeSourceId.set(null);
    this.hoverTarget.set(null);
    this.focusedTarget.set(null);
    this.conversationTarget.set(null);
    this.transaction = null;
  }

  private pushHistory(entry: VpHistoryEntry): void {
    const entries = this.undoEntries();
    const previous = entries.at(-1);
    if (entry.coalesceKey && previous?.coalesceKey === entry.coalesceKey) {
      this.undoEntries.set([
        ...entries.slice(0, -1),
        { ...entry, before: previous.before },
      ]);
    } else {
      this.undoEntries.set([...entries, entry].slice(-this.historyLimit));
    }
    this.redoEntries.set([]);
  }

  private afterMutation(options: VpGraphMutationOptions): void {
    if (options.invalidateValidation !== false) this.validation.set(null);
    if (options.markDirty !== false) this.refreshDirty();
    this.revision.update(value => value + 1);
  }

  private refreshDirty(): void {
    this.dirty.set(graphFingerprint(this.graph()) !== this.savedFingerprint);
  }

  private capturePersistenceIdentity(graph: VpGraph): VpPersistenceIdentity {
    return {
      version: graph.version,
      graphSchemaVersion: graph.graph_schema_version,
      nodeRegistryVersion: graph.node_registry_version,
      definitionRevision: graph.definition_revision,
      baseGraphHash: graph.base_graph_hash,
    };
  }

  private withSaveResult(graph: VpGraph, result: GraphSaveResult): VpGraph {
    return {
      ...cloneGraph(graph),
      id: result.id || graph.id,
      version: result.version || graph.version,
      graph_schema_version: result.graph_schema_version ?? graph.graph_schema_version,
      node_registry_version: result.node_registry_version ?? graph.node_registry_version,
      definition_revision: result.definition_revision,
      base_graph_hash: result.base_graph_hash,
    };
  }

  private withCurrentPersistenceIdentity(graph: VpGraph): VpGraph {
    const restored = cloneGraph(graph);
    restored.version = this.persistenceIdentity.version;
    this.assignOptional(restored, 'graph_schema_version', this.persistenceIdentity.graphSchemaVersion);
    this.assignOptional(restored, 'node_registry_version', this.persistenceIdentity.nodeRegistryVersion);
    this.assignOptional(restored, 'definition_revision', this.persistenceIdentity.definitionRevision);
    this.assignOptional(restored, 'base_graph_hash', this.persistenceIdentity.baseGraphHash);
    return restored;
  }

  private assignOptional<K extends keyof VpGraph>(graph: VpGraph, key: K, value: VpGraph[K] | undefined): void {
    if (value === undefined) delete graph[key];
    else graph[key] = value;
  }
}
