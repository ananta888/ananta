import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  ViewEncapsulation,
  ViewChild,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { SafeHtml } from '@angular/platform-browser';
import { firstValueFrom } from 'rxjs';
import { buildMarkdownDeckArtifactContract } from './markdown-slide-artifacts';
import { MarkdownSlideExportService } from './markdown-slide-export.service';
import { MarkdownSlideMermaidService } from './markdown-slide-mermaid.service';
import { MarkdownSlideRendererService } from './markdown-slide-renderer.service';
import { MarkdownSlideStateService } from './markdown-slide-state.service';
import { MarkdownSlideThemeService } from './markdown-slide-theme.service';
import { MarkdownSlideWorkspaceService } from './markdown-slide-workspace.service';
import {
  MarkdownDeckArtifactContract,
  MarkdownSlideDiagnostic,
  MarkdownSlideRenderResult,
  MermaidBlock,
} from './markdown-slide.models';

@Component({
  standalone: true,
  selector: 'app-markdown-slides',
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  template: `
    <section class="markdown-slides-shell" [class.presentation-active]="presentationMode()" data-testid="markdown-slides-root">
      <header class="markdown-slides-toolbar">
        <div>
          <h2>Markdown Slides</h2>
          <p class="muted">Kova-style decks as plain Markdown artifacts with Hub-governed export boundaries.</p>
        </div>
        <div class="toolbar-actions">
          <button type="button" class="secondary" data-testid="markdown-load-sample" (click)="loadSample()">Sample</button>
          <button type="button" class="secondary" (click)="newDeck()">New</button>
          <button type="button" class="secondary" (click)="previousSlide()" [disabled]="state.state().selectedSlideIndex <= 0">Prev</button>
          <button type="button" class="secondary" data-testid="markdown-next-slide" (click)="nextSlide()" [disabled]="state.state().selectedSlideIndex >= state.state().parseResult.slides.length - 1">Next</button>
          <button type="button" class="secondary" (click)="copyMarkdown()">Copy</button>
          <button type="button" class="secondary" (click)="downloadMarkdown()">Download</button>
          <button type="button" class="primary" data-testid="markdown-presentation-toggle" (click)="togglePresentation()">
            {{ presentationMode() ? 'Exit' : 'Present' }}
          </button>
        </div>
      </header>

      <div class="capability-strip">
        <button type="button" disabled [title]="workspace.disabledReason">Open workspace deck</button>
        <button type="button" disabled [title]="workspace.disabledReason">Save to workspace</button>
        <button type="button" disabled title="Deck generation must be routed through Hub policy and CodeCompass APIs.">Create from context</button>
        <button type="button" disabled title="Deck explanation must be routed through Hub policy and CodeCompass APIs.">Explain deck</button>
        <button type="button" disabled title="Attach actions require a Hub run/artifact API.">Attach to run</button>
        @for (format of exportService.allowedFormats; track format) {
          <button type="button" disabled [title]="exportDisabledReason()">{{ format.toUpperCase() }}</button>
        }
      </div>

      @if (presentationMode()) {
        <div class="presentation-stage" tabindex="0" data-testid="markdown-presentation-stage">
          <button type="button" class="presentation-exit" (click)="togglePresentation()">Exit</button>
          <article
            #presentationHost
            class="slide-frame"
            [style.--slide-bg]="theme().background"
            [style.--slide-fg]="theme().foreground"
            [style.--slide-accent]="theme().accent"
            [style.--slide-code-bg]="theme().codeBackground"
            [style.--slide-padding]="theme().slidePadding">
            <div class="slide-content" [innerHTML]="renderedHtml()"></div>
          </article>
          <nav class="presentation-nav">
            <button type="button" class="secondary" (click)="previousSlide()" [disabled]="state.state().selectedSlideIndex <= 0">Prev</button>
            <span>{{ state.state().selectedSlideIndex + 1 }} / {{ state.state().parseResult.slides.length }}</span>
            <button type="button" class="secondary" (click)="nextSlide()" [disabled]="state.state().selectedSlideIndex >= state.state().parseResult.slides.length - 1">Next</button>
          </nav>
        </div>
      } @else {
        <div class="markdown-slides-grid">
          <aside class="slide-list" data-testid="markdown-slide-list">
            <div class="panel-heading">
              <strong>Slides</strong>
              <span class="muted">{{ state.state().parseResult.slides.length }}</span>
            </div>
            @for (slide of state.state().parseResult.slides; track slide.index) {
              <button
                type="button"
                class="slide-list-item"
                [class.active]="slide.index === state.state().selectedSlideIndex"
                (click)="selectSlide(slide.index)">
                <span>{{ slide.index + 1 }}</span>
                <strong>{{ slide.title }}</strong>
                <small>Lines {{ slide.lineStart }}-{{ slide.lineEnd }}</small>
              </button>
            }
          </aside>

          <section class="editor-panel">
            <label for="markdown-slide-editor">Markdown source</label>
            <textarea
              id="markdown-slide-editor"
              data-testid="markdown-slide-editor"
              [ngModel]="state.state().markdown"
              (ngModelChange)="updateMarkdown($event)"
              spellcheck="false"></textarea>
          </section>

          <section class="preview-panel">
            <div class="panel-heading">
              <strong data-testid="markdown-selected-title">{{ state.selectedSlide()?.title }}</strong>
              <span class="muted">{{ currentThemeLabel() }}</span>
            </div>
            <article
              #previewHost
              class="slide-frame"
              data-testid="markdown-slide-preview"
              [style.--slide-bg]="theme().background"
              [style.--slide-fg]="theme().foreground"
              [style.--slide-accent]="theme().accent"
              [style.--slide-code-bg]="theme().codeBackground"
              [style.--slide-padding]="theme().slidePadding">
              <div class="slide-content" [innerHTML]="renderedHtml()"></div>
            </article>
            <details class="diagnostics-panel" open data-testid="markdown-diagnostics">
              <summary>Diagnostics ({{ diagnostics().length }})</summary>
              @if (diagnostics().length === 0) {
                <p class="muted">No diagnostics.</p>
              } @else {
                @for (diagnostic of diagnostics(); track diagnostic.code + ':' + diagnostic.message + ':' + diagnostic.slideIndex + ':' + diagnostic.line) {
                  <div class="diagnostic" [class.security]="diagnostic.severity === 'security'" [class.error]="diagnostic.severity === 'error'">
                    <strong>{{ diagnostic.severity }}</strong>
                    <span>{{ diagnostic.message }}</span>
                    @if (diagnostic.slideIndex !== undefined) {
                      <small>Slide {{ diagnostic.slideIndex + 1 }}</small>
                    }
                    @if (diagnostic.line) {
                      <small>Line {{ diagnostic.line }}</small>
                    }
                  </div>
                }
              }
            </details>
            <details class="artifact-contract-panel">
              <summary>Artifact contract</summary>
              <pre>{{ artifactContractText() }}</pre>
            </details>
          </section>
        </div>
      }
    </section>
  `,
  styles: [`
    .markdown-slides-shell {
      display: grid;
      gap: 12px;
      min-height: calc(100vh - 92px);
    }
    .markdown-slides-toolbar,
    .capability-strip,
    .panel-heading,
    .presentation-nav {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .markdown-slides-toolbar h2 {
      margin: 0;
      font-size: 22px;
    }
    .markdown-slides-toolbar p {
      margin: 4px 0 0;
      font-size: 13px;
    }
    .toolbar-actions,
    .capability-strip {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .capability-strip {
      justify-content: flex-start;
      padding: 8px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--card-bg);
    }
    .capability-strip button,
    .toolbar-actions button,
    .presentation-nav button {
      min-height: 34px;
      white-space: nowrap;
    }
    .markdown-slides-grid {
      display: grid;
      grid-template-columns: minmax(150px, 220px) minmax(260px, 1fr) minmax(320px, 1.2fr);
      gap: 12px;
      min-height: 0;
    }
    .slide-list,
    .editor-panel,
    .preview-panel {
      display: grid;
      gap: 10px;
      min-width: 0;
      align-content: start;
    }
    .slide-list,
    .editor-panel {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
      background: var(--card-bg);
    }
    .slide-list-item {
      width: 100%;
      display: grid;
      grid-template-columns: 24px minmax(0, 1fr);
      gap: 4px 8px;
      text-align: left;
      background: var(--input-bg);
      border-color: var(--border);
    }
    .slide-list-item span {
      grid-row: span 2;
      display: inline-flex;
      width: 22px;
      height: 22px;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      background: var(--border);
      font-size: 12px;
      font-weight: 700;
    }
    .slide-list-item strong,
    .slide-list-item small {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .slide-list-item.active {
      border-color: var(--accent);
      box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 35%, transparent);
    }
    .editor-panel label {
      font-weight: 700;
    }
    textarea {
      width: 100%;
      min-height: 66vh;
      box-sizing: border-box;
      resize: vertical;
      font: 13px/1.45 ui-monospace, SFMono-Regular, Consolas, monospace;
    }
    .preview-panel {
      min-height: 0;
    }
    .slide-frame {
      width: 100%;
      aspect-ratio: 16 / 9;
      box-sizing: border-box;
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--slide-bg);
      color: var(--slide-fg);
      padding: var(--slide-padding);
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
    }
    .slide-content {
      font-size: 20px;
      line-height: 1.45;
    }
    .slide-content :is(h1, h2, h3) {
      color: var(--slide-fg);
      margin: 0 0 18px;
      line-height: 1.08;
    }
    .slide-content h1 {
      font-size: 42px;
    }
    .slide-content h2 {
      font-size: 34px;
    }
    .slide-content p,
    .slide-content ul,
    .slide-content ol {
      margin-top: 0;
    }
    .slide-content a {
      color: var(--slide-accent);
    }
    .slide-content pre {
      overflow: auto;
      padding: 12px;
      border-radius: 6px;
      background: var(--slide-code-bg);
      font-size: 14px;
    }
    .slide-content code {
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    }
    .slide-content .markdown-slide-mermaid {
      display: grid;
      place-items: center;
      min-height: 120px;
      border: 1px dashed color-mix(in srgb, var(--slide-accent) 55%, transparent);
      border-radius: 6px;
      padding: 10px;
      background: color-mix(in srgb, var(--slide-bg) 90%, white);
    }
    .slide-content .markdown-slide-mermaid svg {
      max-width: 100%;
      height: auto;
    }
    .slide-content .markdown-slide-mermaid-error {
      color: #dc2626;
      border-color: #dc2626;
    }
    .diagnostics-panel,
    .artifact-contract-panel {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
      background: var(--card-bg);
    }
    .diagnostic {
      display: grid;
      grid-template-columns: 80px minmax(0, 1fr) auto auto;
      gap: 8px;
      padding: 6px 0;
      border-top: 1px solid var(--border);
      font-size: 12px;
    }
    .diagnostic.security strong {
      color: #b45309;
    }
    .diagnostic.error strong {
      color: #dc2626;
    }
    .artifact-contract-panel pre {
      max-height: 220px;
      overflow: auto;
      white-space: pre-wrap;
      font-size: 12px;
    }
    .presentation-active {
      position: fixed;
      inset: 0;
      z-index: 1000;
      min-height: 100vh;
      background: var(--bg);
      padding: 14px;
    }
    .presentation-stage {
      display: grid;
      gap: 12px;
      place-items: center;
      min-height: calc(100vh - 28px);
      width: 100%;
    }
    .presentation-stage .slide-frame {
      width: min(1200px, 96vw);
      max-height: 78vh;
    }
    .presentation-exit {
      position: fixed;
      top: 14px;
      right: 14px;
      z-index: 1001;
    }
    .presentation-nav {
      justify-content: center;
    }
    @media (max-width: 1100px) {
      .markdown-slides-grid {
        grid-template-columns: minmax(140px, 190px) minmax(260px, 1fr);
      }
      .preview-panel {
        grid-column: 1 / -1;
      }
    }
    @media (max-width: 760px) {
      .markdown-slides-grid {
        grid-template-columns: minmax(0, 1fr);
      }
      textarea {
        min-height: 44vh;
      }
      .slide-frame {
        padding: 24px;
      }
      .slide-content {
        font-size: 16px;
      }
      .slide-content h1 {
        font-size: 28px;
      }
      .diagnostic {
        grid-template-columns: minmax(0, 1fr);
      }
    }
  `],
})
export class MarkdownSlidesComponent implements AfterViewInit {
  readonly state = inject(MarkdownSlideStateService);
  readonly workspace = inject(MarkdownSlideWorkspaceService);
  readonly exportService = inject(MarkdownSlideExportService);
  private http = inject(HttpClient);
  private renderer = inject(MarkdownSlideRendererService);
  private mermaid = inject(MarkdownSlideMermaidService);
  private themes = inject(MarkdownSlideThemeService);

  @ViewChild('previewHost') previewHost?: ElementRef<HTMLElement>;
  @ViewChild('presentationHost') presentationHost?: ElementRef<HTMLElement>;

  readonly renderedHtml = signal<SafeHtml>('');
  readonly renderDiagnostics = signal<MarkdownSlideDiagnostic[]>([]);
  readonly presentationMode = signal(false);
  readonly artifactContract = signal<MarkdownDeckArtifactContract | null>(null);
  readonly theme = signal(this.themes.resolve().theme);
  private mermaidBlocks: MermaidBlock[] = [];
  private renderToken = 0;

  ngAfterViewInit(): void {
    this.state.restoreDraft();
    void this.renderSelectedSlide();
  }

  diagnostics(): MarkdownSlideDiagnostic[] {
    return [...this.state.diagnostics(), ...this.renderDiagnostics()];
  }

  exportDisabledReason(): string {
    return this.exportService.capability().disabledReason || 'Export backend is unavailable.';
  }

  currentThemeLabel(): string {
    return this.theme().label;
  }

  async loadSample(): Promise<void> {
    const markdown = await firstValueFrom(this.http.get('/assets/sample-decks/ananta-kova-demo.md', { responseType: 'text' }));
    this.state.loadMarkdown(markdown, false);
    await this.renderSelectedSlide();
  }

  newDeck(): void {
    this.state.resetDeck();
    void this.renderSelectedSlide();
  }

  updateMarkdown(markdown: string): void {
    this.state.updateMarkdown(markdown);
    void this.renderSelectedSlide();
  }

  selectSlide(index: number): void {
    this.state.selectSlide(index);
    void this.renderSelectedSlide();
  }

  previousSlide(): void {
    this.state.previousSlide();
    void this.renderSelectedSlide();
  }

  nextSlide(): void {
    this.state.nextSlide();
    void this.renderSelectedSlide();
  }

  togglePresentation(): void {
    this.presentationMode.update(value => !value);
    setTimeout(() => this.renderMermaid(), 0);
  }

  copyMarkdown(): void {
    navigator.clipboard?.writeText(this.state.state().markdown).catch(() => {});
  }

  downloadMarkdown(): void {
    const blob = new Blob([this.state.state().markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'ananta-markdown-deck.md';
    link.click();
    URL.revokeObjectURL(url);
  }

  artifactContractText(): string {
    const contract = this.artifactContract();
    return contract ? JSON.stringify(contract, null, 2) : 'Artifact contract is being calculated.';
  }

  @HostListener('document:keydown', ['$event'])
  onKeydown(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null;
    const editing = ['TEXTAREA', 'INPUT', 'SELECT'].includes(target?.tagName || '');
    if (editing && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) return;
    if (event.key === 'ArrowLeft' || event.key === 'PageUp') {
      event.preventDefault();
      this.previousSlide();
    }
    if (event.key === 'ArrowRight' || event.key === 'PageDown') {
      event.preventDefault();
      this.nextSlide();
    }
    if (event.key === 'Escape' && this.presentationMode()) {
      event.preventDefault();
      this.presentationMode.set(false);
      setTimeout(() => this.renderMermaid(), 0);
    }
  }

  private async renderSelectedSlide(): Promise<void> {
    const token = ++this.renderToken;
    const selected = this.state.selectedSlide();
    const themeResult = this.themes.resolve(this.state.state().parseResult.metadata.theme);
    this.theme.set(themeResult.theme);
    const result = await this.renderer.render(selected?.rawMarkdown || '', {
      slideIndex: selected?.index || 0,
      theme: themeResult.theme,
    });
    if (token !== this.renderToken) return;
    this.applyRenderResult(result, themeResult.diagnostic ? [themeResult.diagnostic] : []);
    await this.updateArtifactContract();
    setTimeout(() => this.renderMermaid(), 0);
  }

  private applyRenderResult(result: MarkdownSlideRenderResult, extraDiagnostics: MarkdownSlideDiagnostic[]): void {
    this.renderedHtml.set(result.html);
    this.mermaidBlocks = result.mermaidBlocks;
    this.renderDiagnostics.set([...result.diagnostics, ...extraDiagnostics]);
  }

  private async renderMermaid(): Promise<void> {
    const host = this.presentationMode()
      ? this.presentationHost?.nativeElement
      : this.previewHost?.nativeElement;
    if (!host) return;
    const diagnostics = await this.mermaid.renderInto(host, this.mermaidBlocks);
    if (diagnostics.length) {
      this.renderDiagnostics.update(current => [...current, ...diagnostics]);
    }
  }

  private async updateArtifactContract(): Promise<void> {
    const state = this.state.state();
    const contract = await buildMarkdownDeckArtifactContract(
      state.markdown,
      state.parseResult.metadata,
      state.parseResult.slides.length,
      this.diagnostics(),
    );
    this.artifactContract.set(contract);
  }
}
