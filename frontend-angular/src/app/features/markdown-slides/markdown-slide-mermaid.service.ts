import { Injectable } from '@angular/core';
import DOMPurify from 'dompurify';
import { MarkdownSlideDiagnostic, MermaidBlock } from './markdown-slide.models';

@Injectable({ providedIn: 'root' })
export class MarkdownSlideMermaidService {
  private initPromise: Promise<void> | null = null;

  async renderInto(host: HTMLElement, blocks: MermaidBlock[]): Promise<MarkdownSlideDiagnostic[]> {
    const diagnostics: MarkdownSlideDiagnostic[] = [];
    if (!blocks.length) return diagnostics;
    await this.ensureMermaid();
    const mermaid = (await import('mermaid')).default;

    for (const block of blocks) {
      const target = host.querySelector(`[data-mermaid-id="${block.id}"]`) as HTMLElement | null;
      if (!target) continue;
      try {
        const { svg } = await mermaid.render(block.id, block.code);
        target.innerHTML = DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true } });
      } catch (error: any) {
        target.textContent = 'Mermaid render error';
        target.classList.add('markdown-slide-mermaid-error');
        diagnostics.push({
          severity: 'error',
          code: 'mermaid_render_error',
          message: String(error?.message || error || 'Mermaid render error'),
          slideIndex: block.slideIndex,
        });
      }
    }

    return diagnostics;
  }

  private ensureMermaid(): Promise<void> {
    if (this.initPromise) return this.initPromise;
    this.initPromise = import('mermaid').then(module => {
      module.default.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'default',
      });
    });
    return this.initPromise;
  }
}
