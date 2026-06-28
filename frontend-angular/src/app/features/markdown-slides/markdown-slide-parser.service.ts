import { Injectable } from '@angular/core';
import {
  MarkdownDeckMetadata,
  MarkdownDeckParseResult,
  MarkdownSlide,
  MarkdownSlideDiagnostic,
} from './markdown-slide.models';

@Injectable({ providedIn: 'root' })
export class MarkdownSlideParserService {
  parse(markdown: string): MarkdownDeckParseResult {
    const normalized = String(markdown ?? '').replace(/\r\n?/g, '\n');
    const lines = normalized.split('\n');
    const diagnostics: MarkdownSlideDiagnostic[] = [];
    const frontmatter = this.readFrontmatter(lines);
    const metadata = this.parseMetadata(frontmatter.lines);
    const separators = this.findSeparators(lines, frontmatter.endLineExclusive);
    const slides = this.buildSlides(lines, frontmatter.endLineExclusive, separators, diagnostics);

    if (slides.length === 0) {
      slides.push(this.createSlide(0, '', Math.max(1, frontmatter.endLineExclusive + 1), Math.max(1, frontmatter.endLineExclusive + 1)));
      diagnostics.push({
        severity: 'warning',
        code: 'empty_deck',
        message: 'Deck contains no slide content.',
        slideIndex: 0,
        line: 1,
      });
    }

    return {
      metadata: { ...metadata, diagnostics },
      slides,
      diagnostics,
    };
  }

  private readFrontmatter(lines: string[]): { lines: string[]; endLineExclusive: number } {
    if (lines[0]?.trim() !== '---') return { lines: [], endLineExclusive: 0 };
    const closingIndex = lines.findIndex((line, index) => index > 0 && line.trim() === '---');
    if (closingIndex <= 0) return { lines: [], endLineExclusive: 0 };
    return {
      lines: lines.slice(1, closingIndex),
      endLineExclusive: closingIndex + 1,
    };
  }

  private parseMetadata(lines: string[]): MarkdownDeckMetadata {
    const metadata: MarkdownDeckMetadata = {};
    for (const line of lines) {
      const match = line.match(/^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$/);
      if (!match) continue;
      const key = match[1];
      const value = match[2].trim().replace(/^['"]|['"]$/g, '');
      if (key === 'title') metadata.title = value;
      if (key === 'author') metadata.author = value;
      if (key === 'theme') metadata.theme = value;
      if (key === 'aspectRatio') metadata.aspectRatio = value;
      if (key === 'createdAt') metadata.createdAt = value;
      if (key === 'updatedAt') metadata.updatedAt = value;
      if (key === 'sourcePath') metadata.sourcePath = value;
      if (key === 'deckId') metadata.deckId = value;
    }
    return metadata;
  }

  private findSeparators(lines: string[], startIndex: number): number[] {
    const separators: number[] = [];
    let fence: string | null = null;
    for (let index = startIndex; index < lines.length; index += 1) {
      const line = lines[index];
      const fenceMatch = line.match(/^\s*(```|~~~)/);
      if (fenceMatch) {
        const marker = fenceMatch[1];
        fence = fence === marker ? null : fence || marker;
      }
      if (!fence && line.trim() === '---') {
        separators.push(index);
      }
    }
    return separators;
  }

  private buildSlides(
    lines: string[],
    contentStart: number,
    separators: number[],
    diagnostics: MarkdownSlideDiagnostic[],
  ): MarkdownSlide[] {
    const boundaries = [...separators, lines.length];
    const slides: MarkdownSlide[] = [];
    let start = contentStart;

    boundaries.forEach((end, index) => {
      const rawLines = lines.slice(start, end);
      const lineStart = start + 1;
      const lineEnd = Math.max(lineStart, end);
      const slide = this.createSlide(index, rawLines.join('\n').trim(), lineStart, lineEnd);
      if (!slide.rawMarkdown.trim()) {
        const diagnostic: MarkdownSlideDiagnostic = {
          severity: 'warning',
          code: 'empty_slide',
          message: `Slide ${index + 1} is empty.`,
          slideIndex: index,
          line: lineStart,
        };
        slide.diagnostics.push(diagnostic);
        diagnostics.push(diagnostic);
      }
      slides.push(slide);
      start = end + 1;
    });

    return slides;
  }

  private createSlide(index: number, rawMarkdown: string, lineStart: number, lineEnd: number): MarkdownSlide {
    return {
      index,
      rawMarkdown,
      title: this.guessTitle(rawMarkdown, index),
      lineStart,
      lineEnd,
      diagnostics: [],
    };
  }

  private guessTitle(markdown: string, index: number): string {
    const heading = markdown.split('\n').find(line => /^#{1,3}\s+\S/.test(line.trim()));
    if (heading) return heading.replace(/^#{1,3}\s+/, '').trim();
    const first = markdown.split('\n').map(line => line.trim()).find(Boolean);
    return first ? first.slice(0, 60) : `Slide ${index + 1}`;
  }
}
