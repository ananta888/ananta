import { Injectable } from '@angular/core';

import { GeoMapDraft } from './geomap.models';

const KEY = 'ananta.geomap.draft.v1';

@Injectable({ providedIn: 'root' })
export class GeoMapDraftStore {
  load(): GeoMapDraft | null {
    try {
      const value = JSON.parse(localStorage.getItem(KEY) || 'null') as Partial<GeoMapDraft> | null;
      if (value?.schema !== 'ananta.geomap-draft.v1' || typeof value.mapId !== 'string') return null;
      return value as GeoMapDraft;
    } catch {
      return null;
    }
  }

  save(draft: GeoMapDraft): void {
    localStorage.setItem(KEY, JSON.stringify(draft));
  }

  clear(): void {
    localStorage.removeItem(KEY);
  }
}
