import { MarkdownSlideMermaidService } from './markdown-slide-mermaid.service';

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (id: string, code: string) => {
      if (code.includes('INVALID')) throw new Error('Invalid Mermaid syntax');
      return { svg: `<svg><script>alert(1)</script><text>${id}</text></svg>` };
    }),
  },
}));

describe('MarkdownSlideMermaidService', () => {
  it('renders multiple diagrams with unique targets and sanitizes SVG', async () => {
    const host = document.createElement('div');
    host.innerHTML = '<div data-mermaid-id="a"></div><div data-mermaid-id="b"></div>';
    const service = new MarkdownSlideMermaidService();

    const diagnostics = await service.renderInto(host, [
      { id: 'a', code: 'graph TD\nA-->B', slideIndex: 0 },
      { id: 'b', code: 'graph TD\nB-->C', slideIndex: 0 },
    ]);

    expect(diagnostics).toEqual([]);
    expect(host.innerHTML).toContain('<svg');
    expect(host.innerHTML).not.toContain('<script>');
  });

  it('reports invalid Mermaid as a non-fatal diagnostic', async () => {
    const host = document.createElement('div');
    host.innerHTML = '<div data-mermaid-id="bad"></div>';
    const service = new MarkdownSlideMermaidService();

    const diagnostics = await service.renderInto(host, [
      { id: 'bad', code: 'INVALID', slideIndex: 1 },
    ]);

    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0].code).toBe('mermaid_render_error');
    expect(host.textContent).toContain('Mermaid render error');
  });
});
