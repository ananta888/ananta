import { computed, Injectable, inject, signal } from '@angular/core';
import { MarkdownDeckState } from './markdown-slide.models';
import { MarkdownSlideParserService } from './markdown-slide-parser.service';
import { LocalMarkdownDeckPersistenceService } from './markdown-slide-persistence.service';

const DEFAULT_DECK = `---
title: Untitled Ananta deck
theme: ananta-default
---

# Untitled Ananta deck

Start writing Markdown. Use standalone --- lines to split slides.
`;

@Injectable({ providedIn: 'root' })
export class MarkdownSlideStateService {
  private parser = inject(MarkdownSlideParserService);
  private persistence = inject(LocalMarkdownDeckPersistenceService);

  readonly state = signal<MarkdownDeckState>(this.createState(DEFAULT_DECK, 0, false));
  readonly selectedSlide = computed(() => this.state().parseResult.slides[this.state().selectedSlideIndex] || this.state().parseResult.slides[0]);
  readonly diagnostics = computed(() => {
    const state = this.state();
    return [
      ...state.parseResult.diagnostics,
      ...state.parseResult.slides.flatMap(slide => slide.diagnostics),
      ...(state.persistenceDiagnostic ? [state.persistenceDiagnostic] : []),
    ];
  });

  restoreDraft(): void {
    const loaded = this.persistence.loadDraft();
    if (!loaded.snapshot) {
      if (loaded.diagnostic) {
        this.state.update(state => ({ ...state, persistenceDiagnostic: loaded.diagnostic }));
      }
      return;
    }
    this.state.set(this.createState(loaded.snapshot.markdown, loaded.snapshot.selectedSlideIndex, false, loaded.diagnostic));
  }

  loadMarkdown(markdown: string, dirty = false): void {
    this.state.set(this.createState(markdown, 0, dirty));
    this.persist();
  }

  updateMarkdown(markdown: string): void {
    const previous = this.state();
    this.state.set(this.createState(markdown, previous.selectedSlideIndex, true));
    this.persist();
  }

  selectSlide(index: number): void {
    const state = this.state();
    const bounded = Math.min(Math.max(0, index), Math.max(0, state.parseResult.slides.length - 1));
    this.state.set({ ...state, selectedSlideIndex: bounded });
    this.persist();
  }

  nextSlide(): void {
    this.selectSlide(this.state().selectedSlideIndex + 1);
  }

  previousSlide(): void {
    this.selectSlide(this.state().selectedSlideIndex - 1);
  }

  resetDeck(): void {
    this.persistence.clearDraft();
    this.state.set(this.createState(DEFAULT_DECK, 0, false));
  }

  persist(): void {
    const state = this.state();
    this.persistence.saveDraft({
      markdown: state.markdown,
      selectedSlideIndex: state.selectedSlideIndex,
      updatedAt: new Date().toISOString(),
    });
  }

  private createState(markdown: string, selectedSlideIndex: number, dirty: boolean, persistenceDiagnostic?: any): MarkdownDeckState {
    const parseResult = this.parser.parse(markdown);
    const bounded = Math.min(Math.max(0, selectedSlideIndex), Math.max(0, parseResult.slides.length - 1));
    return {
      markdown,
      parseResult,
      selectedSlideIndex: bounded,
      dirty,
      persistenceDiagnostic,
    };
  }
}
