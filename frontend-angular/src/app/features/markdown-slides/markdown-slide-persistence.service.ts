import { Injectable } from '@angular/core';
import {
  MarkdownDeckPersistenceAdapter,
  MarkdownDeckPersistenceSnapshot,
  MarkdownSlideDiagnostic,
} from './markdown-slide.models';

const STORAGE_KEY = 'ananta.markdownSlides.draft.v1';

@Injectable({ providedIn: 'root' })
export class LocalMarkdownDeckPersistenceService implements MarkdownDeckPersistenceAdapter {
  loadDraft(): { snapshot: MarkdownDeckPersistenceSnapshot | null; diagnostic?: MarkdownSlideDiagnostic } {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { snapshot: null };
    try {
      const parsed = JSON.parse(raw) as Partial<MarkdownDeckPersistenceSnapshot>;
      if (typeof parsed.markdown !== 'string') throw new Error('missing markdown');
      return {
        snapshot: {
          markdown: parsed.markdown,
          selectedSlideIndex: Number.isFinite(parsed.selectedSlideIndex) ? Number(parsed.selectedSlideIndex) : 0,
          updatedAt: typeof parsed.updatedAt === 'string' ? parsed.updatedAt : new Date().toISOString(),
        },
      };
    } catch {
      localStorage.removeItem(STORAGE_KEY);
      return {
        snapshot: null,
        diagnostic: {
          severity: 'warning',
          code: 'draft_storage_corrupt',
          message: 'Stored Markdown slide draft was corrupt and has been ignored.',
        },
      };
    }
  }

  saveDraft(snapshot: MarkdownDeckPersistenceSnapshot): void {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
  }

  clearDraft(): void {
    localStorage.removeItem(STORAGE_KEY);
  }
}
