import { MarkdownSlideParserService } from './markdown-slide-parser.service';

describe('MarkdownSlideParserService', () => {
  const parser = new MarkdownSlideParserService();

  it('splits standalone separators into stable slides', () => {
    const result = parser.parse('# One\n\n---\n\n# Two\n\n---\n\n# Three');

    expect(result.slides).toHaveLength(3);
    expect(result.slides.map(slide => slide.title)).toEqual(['One', 'Two', 'Three']);
    expect(result.slides[0].lineStart).toBe(1);
    expect(result.slides[1].lineStart).toBe(4);
    expect(result.slides[2].lineStart).toBe(8);
  });

  it('parses frontmatter as metadata instead of a slide', () => {
    const result = parser.parse('---\ntitle: Demo\nauthor: Ananta\ntheme: clean-docs\n---\n\n# First');

    expect(result.metadata.title).toBe('Demo');
    expect(result.metadata.author).toBe('Ananta');
    expect(result.metadata.theme).toBe('clean-docs');
    expect(result.slides).toHaveLength(1);
    expect(result.slides[0].title).toBe('First');
  });

  it('ignores separators inside fenced code blocks', () => {
    const result = parser.parse('# One\n\n```text\n---\n```\n\n---\n\n# Two');

    expect(result.slides).toHaveLength(2);
    expect(result.slides[0].rawMarkdown).toContain('---');
  });

  it('warns about empty slides without crashing', () => {
    const result = parser.parse('# One\n---\n\n---\n# Three');

    expect(result.slides).toHaveLength(3);
    expect(result.diagnostics.some(diagnostic => diagnostic.code === 'empty_slide')).toBe(true);
  });
});
