import {
  MarkdownDeckArtifactContract,
  MarkdownDeckMetadata,
  MarkdownSlideDiagnostic,
  MarkdownSlideDiagnosticSeverity,
} from './markdown-slide.models';

export async function buildMarkdownDeckArtifactContract(
  markdown: string,
  metadata: MarkdownDeckMetadata,
  slideCount: number,
  diagnostics: MarkdownSlideDiagnostic[],
): Promise<MarkdownDeckArtifactContract> {
  return {
    artifactType: 'markdown_slide_deck',
    sourcePath: metadata.sourcePath,
    contentHash: await sha256(markdown),
    metadata,
    slideCount,
    diagnosticsSummary: summarizeDiagnostics(diagnostics),
  };
}

export function summarizeDiagnostics(diagnostics: MarkdownSlideDiagnostic[]): Record<MarkdownSlideDiagnosticSeverity, number> {
  return diagnostics.reduce((summary, diagnostic) => {
    summary[diagnostic.severity] += 1;
    return summary;
  }, { info: 0, warning: 0, error: 0, security: 0 } as Record<MarkdownSlideDiagnosticSeverity, number>);
}

async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest)).map(byte => byte.toString(16).padStart(2, '0')).join('');
}
