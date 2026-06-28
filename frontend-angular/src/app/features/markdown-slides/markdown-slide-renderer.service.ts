import { Injectable, inject } from '@angular/core';
import { DomSanitizer } from '@angular/platform-browser';
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import {
  MarkdownSlideDiagnostic,
  MarkdownSlideRenderOptions,
  MarkdownSlideRenderResult,
  MermaidBlock,
} from './markdown-slide.models';

@Injectable({ providedIn: 'root' })
export class MarkdownSlideRendererService {
  private sanitizer = inject(DomSanitizer);
  private renderSequence = 0;

  async render(markdown: string, options: MarkdownSlideRenderOptions): Promise<MarkdownSlideRenderResult> {
    const diagnostics = this.securityDiagnostics(markdown, options.slideIndex);
    const mermaidBlocks: MermaidBlock[] = [];
    const markdownWithPlaceholders = this.extractMermaid(markdown, options.slideIndex, mermaidBlocks);
    const rawHtml = await marked.parse(markdownWithPlaceholders, {
      async: false,
      breaks: false,
      gfm: true,
    }) as string;
    const sanitizedHtml = DOMPurify.sanitize(rawHtml, {
      USE_PROFILES: { html: true },
      FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'input', 'button'],
      FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover', 'style'],
      ADD_ATTR: ['data-mermaid-id'],
    });

    if (rawHtml !== sanitizedHtml && diagnostics.length === 0) {
      diagnostics.push({
        severity: 'security',
        code: 'sanitized_html',
        message: 'Unsafe HTML was removed from the rendered slide.',
        slideIndex: options.slideIndex,
      });
    }

    return {
      html: this.sanitizer.bypassSecurityTrustHtml(sanitizedHtml),
      sanitizedHtml,
      mermaidBlocks,
      diagnostics,
    };
  }

  private extractMermaid(markdown: string, slideIndex: number, blocks: MermaidBlock[]): string {
    return markdown.replace(/```mermaid\s*\n([\s\S]*?)```/gi, (_match, code: string) => {
      const id = `markdown-slide-${slideIndex}-mermaid-${++this.renderSequence}`;
      blocks.push({ id, code: String(code || '').trim(), slideIndex });
      return `<div class="markdown-slide-mermaid" data-mermaid-id="${id}"></div>`;
    });
  }

  private securityDiagnostics(markdown: string, slideIndex: number): MarkdownSlideDiagnostic[] {
    const diagnostics: MarkdownSlideDiagnostic[] = [];
    const checks: Array<[RegExp, string, string]> = [
      [/<\s*script\b/i, 'script_removed', 'Script tags are not allowed in Markdown slides.'],
      [/\son[a-z]+\s*=/i, 'event_attribute_removed', 'Inline event handlers are not allowed in Markdown slides.'],
      [/javascript\s*:/i, 'javascript_url_removed', 'javascript: URLs are not allowed in Markdown slides.'],
      [/<\s*(iframe|object|embed)\b/i, 'embed_removed', 'Embedded active objects are not allowed in Markdown slides.'],
    ];
    for (const [pattern, code, message] of checks) {
      if (!pattern.test(markdown)) continue;
      diagnostics.push({ severity: 'security', code, message, slideIndex });
    }
    return diagnostics;
  }
}
