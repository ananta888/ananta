import { InjectionToken, Signal, WritableSignal } from '@angular/core';

import {
  GraphSaveResult,
  ValidationResult,
  VpGraph,
} from './visual-process-api.service';
import { CanvasHitTarget } from './vp-editor-context.models';

export interface VpGraphMutationOptions {
  coalesceKey?: string;
  invalidateValidation?: boolean;
  markDirty?: boolean;
  recordHistory?: boolean;
}

export interface VpEditorCommand {
  label: string;
  apply(graph: VpGraph): VpGraph;
  coalesceKey?: string;
}

/** Immutable request boundary used to correlate an asynchronous save result. */
export interface VpEditorSaveRequest {
  readonly graph: VpGraph;
  readonly revision: number;
  readonly state_epoch: number;
  readonly request_id: number;
}

export type VpEditorSaveAcceptance =
  | { readonly status: 'accepted_clean'; readonly request_id: number }
  | { readonly status: 'accepted_dirty'; readonly request_id: number }
  | { readonly status: 'rejected_stale'; readonly request_id: number }
  | { readonly status: 'rejected_identity'; readonly request_id: number };

/** Keeps hosted persistence alive when an editor view is destroyed. */
export interface VpEditorPersistencePort {
  saveCurrentGraph(): void;
}

/**
 * State aggregate consumed by an editor surface.
 *
 * A standalone editor resolves its own implementation. A hosting workspace may
 * provide this port once so multiple views edit the same canonical VpGraph.
 */
export interface VpEditorStatePort {
  readonly graph: WritableSignal<VpGraph>;
  readonly selectedId: WritableSignal<string | null>;
  readonly dirty: WritableSignal<boolean>;
  readonly validation: WritableSignal<ValidationResult | null>;
  readonly edgeMode: WritableSignal<boolean>;
  readonly edgeSourceId: WritableSignal<string | null>;
  readonly revision: WritableSignal<number>;
  readonly hoverTarget: WritableSignal<CanvasHitTarget | null>;
  readonly focusedTarget: WritableSignal<CanvasHitTarget | null>;
  readonly conversationTarget: WritableSignal<CanvasHitTarget | null>;
  readonly canUndo: Signal<boolean>;
  readonly canRedo: Signal<boolean>;
  readonly undoLabel: Signal<string>;
  readonly redoLabel: Signal<string>;

  initialize(graph: VpGraph): void;
  replaceGraph(
    graph: VpGraph,
    options?: { markDirty?: boolean; validation?: ValidationResult | null; resetHistory?: boolean },
  ): void;
  execute(
    label: string,
    reducer: (graph: VpGraph) => VpGraph,
    options?: VpGraphMutationOptions,
  ): boolean;
  dispatch(command: VpEditorCommand, options?: VpGraphMutationOptions): boolean;
  mutate(
    label: string,
    mutator: (draft: VpGraph) => void,
    options?: VpGraphMutationOptions,
  ): boolean;
  beginTransaction(label: string): void;
  commitTransaction(): boolean;
  cancelTransaction(): void;
  undo(): boolean;
  redo(): boolean;
  markSaved(): void;
  captureSaveRequest(): VpEditorSaveRequest;
  acceptSaveResult(
    result: GraphSaveResult,
    request: VpEditorSaveRequest,
  ): VpEditorSaveAcceptance;
  previewTarget(target: CanvasHitTarget | null): void;
  focusTarget(target: CanvasHitTarget | null): void;
  freezeConversationTarget(target?: CanvasHitTarget | null): void;
  clearConversationTarget(): void;
  destroy(): void;
}

/** Parent workspaces provide this token; editor components never own it. */
export const VP_EDITOR_STATE = new InjectionToken<VpEditorStatePort>('VP_EDITOR_STATE');

export const VP_EDITOR_PERSISTENCE = new InjectionToken<VpEditorPersistencePort>(
  'VP_EDITOR_PERSISTENCE',
);
