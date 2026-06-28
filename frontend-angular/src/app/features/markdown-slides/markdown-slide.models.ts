import { SafeHtml } from '@angular/platform-browser';

export type MarkdownSlideDiagnosticSeverity = 'info' | 'warning' | 'error' | 'security';
export type MarkdownSlideExportFormat = 'html' | 'pdf' | 'pptx';
export type MarkdownSlideExportJobStatus = 'unsupported' | 'queued' | 'running' | 'succeeded' | 'failed';

export interface MarkdownSlideDiagnostic {
  severity: MarkdownSlideDiagnosticSeverity;
  code: string;
  message: string;
  slideIndex?: number;
  line?: number;
}

export interface MarkdownDeckMetadata {
  title?: string;
  author?: string;
  theme?: string;
  aspectRatio?: string;
  createdAt?: string;
  updatedAt?: string;
  sourcePath?: string;
  deckId?: string;
  diagnostics?: MarkdownSlideDiagnostic[];
}

export interface MarkdownSlide {
  index: number;
  rawMarkdown: string;
  title: string;
  lineStart: number;
  lineEnd: number;
  diagnostics: MarkdownSlideDiagnostic[];
}

export interface MarkdownDeckParseResult {
  metadata: MarkdownDeckMetadata;
  slides: MarkdownSlide[];
  diagnostics: MarkdownSlideDiagnostic[];
}

export interface MermaidBlock {
  id: string;
  code: string;
  slideIndex: number;
}

export interface MarkdownSlideRenderResult {
  html: SafeHtml;
  sanitizedHtml: string;
  mermaidBlocks: MermaidBlock[];
  diagnostics: MarkdownSlideDiagnostic[];
}

export interface MarkdownSlideRenderOptions {
  slideIndex: number;
  theme: MarkdownSlideTheme;
}

export interface MarkdownSlideTheme {
  id: string;
  label: string;
  background: string;
  foreground: string;
  accent: string;
  codeBackground: string;
  slidePadding: string;
}

export interface MarkdownDeckState {
  markdown: string;
  parseResult: MarkdownDeckParseResult;
  selectedSlideIndex: number;
  dirty: boolean;
  persistenceDiagnostic?: MarkdownSlideDiagnostic;
}

export interface MarkdownDeckArtifactContract {
  artifactType: 'markdown_slide_deck';
  sourcePath?: string;
  contentHash: string;
  metadata: MarkdownDeckMetadata;
  slideCount: number;
  diagnosticsSummary: Record<MarkdownSlideDiagnosticSeverity, number>;
  createdBy?: string;
  updatedBy?: string;
}

export interface MarkdownDeckPersistenceSnapshot {
  markdown: string;
  selectedSlideIndex: number;
  updatedAt: string;
}

export interface MarkdownDeckPersistenceAdapter {
  loadDraft(): { snapshot: MarkdownDeckPersistenceSnapshot | null; diagnostic?: MarkdownSlideDiagnostic };
  saveDraft(snapshot: MarkdownDeckPersistenceSnapshot): void;
  clearDraft(): void;
}

export interface MarkdownWorkspaceDeckAdapter {
  readonly supported: boolean;
  readonly disabledReason: string;
  loadMarkdownDeck(sourcePath: string): Promise<string>;
  saveMarkdownDeck(sourcePath: string, markdown: string): Promise<void>;
}

export interface MarkdownSlideExportOptions {
  format: MarkdownSlideExportFormat;
  theme: string;
  includeSpeakerNotes?: boolean;
}

export interface MarkdownSlideExportJobRequest {
  deckArtifactId: string;
  format: MarkdownSlideExportFormat;
  theme: string;
  options: MarkdownSlideExportOptions;
}

export interface MarkdownSlideExportJob {
  jobId: string;
  status: MarkdownSlideExportJobStatus;
  outputArtifactId?: string;
  logs: string[];
  warnings: string[];
  error?: string;
}

export interface MarkdownSlideExportCapability {
  supportedFormats: MarkdownSlideExportFormat[];
  disabledReason?: string;
}
