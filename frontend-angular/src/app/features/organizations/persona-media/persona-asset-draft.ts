import { signal } from '@angular/core';
import { PersonaAssetReference, PersonaSelection, PersonaSelectionState, PersonaStoredAssetKind } from './persona-profile.models';

/** Local metadata draft: no HTTP, publication, policy or inherited resolution. */
export class PersonaAssetDraft<K extends PersonaStoredAssetKind> {
  readonly state = signal<PersonaSelectionState>('missing');
  readonly id = signal('');
  readonly asset = signal<PersonaAssetReference<K> | null>(null);

  reset(selection?: PersonaSelection<K>): void {
    this.state.set(selection?.state ?? 'missing');
    this.asset.set(selection?.asset ?? null);
    this.id.set(selection?.asset?.artifact_id ?? '');
  }
  selectState(state: PersonaSelectionState): void {
    this.state.set(state); this.asset.set(null); this.id.set('');
  }
  changeId(id: string): void { this.id.set(id); this.asset.set(null); }
  selection(): PersonaSelection<K> {
    return { state: this.state(), asset: this.state() === 'asset' ? this.asset() : null };
  }
}
