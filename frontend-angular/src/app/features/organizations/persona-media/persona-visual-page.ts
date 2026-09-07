import { signal } from '@angular/core';
import { PersonaAssetReference } from './persona-profile.models';

/** One replaceable private discovery page, independent of the selected draft. */
export class PersonaVisualPage<K extends 'image' | 'video'> {
  readonly items = signal<readonly PersonaAssetReference<K>[]>([]);
  readonly cursor = signal<string | null>(null);
  readonly loaded = signal(false);

  reset(): void {
    this.items.set([]);
    this.cursor.set(null);
    this.loaded.set(false);
  }
}
