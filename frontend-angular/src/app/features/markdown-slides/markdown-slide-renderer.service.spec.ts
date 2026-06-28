import { TestBed } from '@angular/core/testing';
import { MarkdownSlideRendererService } from './markdown-slide-renderer.service';
import { MarkdownSlideThemeService } from './markdown-slide-theme.service';
import { buildMarkdownDeckArtifactContract } from './markdown-slide-artifacts';

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (id: string, code: string) => {
      if (code.includes('INVALID')) throw new Error('Invalid Mermaid syntax');
      return { svg: `<svg><script>alert(1)</script><text>${id}</text></svg>` };
    }),
  },
}));

describe('MarkdownSlideRendererService', () => {
  let renderer: MarkdownSlideRendererService;
  let themes: MarkdownSlideThemeService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    renderer = TestBed.inject(MarkdownSlideRendererService);
    themes = TestBed.inject(MarkdownSlideThemeService);
  });

  it('renders headings and code fences', async () => {
    const result = await renderer.render('# Heading\n\n```ts\nconst x = 1;\n```', {
      slideIndex: 0,
      theme: themes.resolve().theme,
    });

    expect(result.sanitizedHtml).toContain('<h1>Heading</h1>');
    expect(result.sanitizedHtml).toContain('<pre>');
    expect(result.sanitizedHtml).toContain('const x = 1');
  });

  it('sanitizes unsafe HTML, event handlers, and javascript links', async () => {
    const result = await renderer.render(
      '# X\n<script>alert(1)</script>\n<a href="javascript:alert(1)" onclick="alert(2)">bad</a>',
      { slideIndex: 1, theme: themes.resolve().theme },
    );

    expect(result.sanitizedHtml).not.toContain('<script>');
    expect(result.sanitizedHtml).not.toContain('onclick');
    expect(result.sanitizedHtml).not.toContain('javascript:');
    expect(result.diagnostics.map(diagnostic => diagnostic.code)).toContain('script_removed');
    expect(result.diagnostics.map(diagnostic => diagnostic.code)).toContain('event_attribute_removed');
    expect(result.diagnostics.map(diagnostic => diagnostic.code)).toContain('javascript_url_removed');
  });

  it('extracts Mermaid fences into stable placeholders', async () => {
    const result = await renderer.render('```mermaid\ngraph TD\nA-->B\n```', {
      slideIndex: 2,
      theme: themes.resolve().theme,
    });

    expect(result.mermaidBlocks).toHaveLength(1);
    expect(result.mermaidBlocks[0].code).toContain('graph TD');
    expect(result.sanitizedHtml).toContain('data-mermaid-id');
  });
});

describe('markdown slide artifact contract', () => {
  it('keeps source provenance and diagnostic summaries', async () => {
    const contract = await buildMarkdownDeckArtifactContract(
      '# Deck',
      { title: 'Deck', sourcePath: 'workspace/deck.md' },
      1,
      [{ severity: 'security', code: 'x', message: 'blocked' }],
    );

    expect(contract.artifactType).toBe('markdown_slide_deck');
    expect(contract.sourcePath).toBe('workspace/deck.md');
    expect(contract.contentHash).toMatch(/^[a-f0-9]{64}$/);
    expect(contract.diagnosticsSummary.security).toBe(1);
  });
});
