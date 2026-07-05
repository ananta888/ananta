import {
  MarkdownDeckArtifactContract,
  MarkdownDeckMetadata,
  MarkdownSlideDiagnostic,
  MarkdownSlideDiagnosticSeverity,
} from './markdown-slide.models';
import { sha256Hex } from '../../shared/crypto/sha256';

export async function buildMarkdownDeckArtifactContract(
  markdown: string,
  metadata: MarkdownDeckMetadata,
  slideCount: number,
  diagnostics: MarkdownSlideDiagnostic[],
): Promise<MarkdownDeckArtifactContract> {
  return {
    artifactType: 'markdown_slide_deck',
    sourcePath: metadata.sourcePath,
    contentHash: await sha256Hex(markdown),
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
