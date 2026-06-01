import { TestBed } from '@angular/core/testing';
import { ControlCenterMarkdownMermaidViewerComponent } from './control-center-artifact-viewers.component';

describe('ControlCenterMarkdownMermaidViewerComponent security', () => {
  it('sanitizes script tags from markdown render output', async () => {
    await TestBed.configureTestingModule({
      imports: [ControlCenterMarkdownMermaidViewerComponent],
    }).compileComponents();

    const fixture = TestBed.createComponent(ControlCenterMarkdownMermaidViewerComponent);
    fixture.componentInstance.type = 'markdown';
    fixture.componentInstance.source = '# Hello\n<script>alert(1)</script>**safe**';
    fixture.detectChanges();

    const html = fixture.nativeElement.innerHTML as string;
    expect(html.toLowerCase()).not.toContain('<script');
    expect(html).toContain('safe');
  });

  it('keeps mermaid source visible as fallback', async () => {
    await TestBed.configureTestingModule({
      imports: [ControlCenterMarkdownMermaidViewerComponent],
    }).compileComponents();

    const fixture = TestBed.createComponent(ControlCenterMarkdownMermaidViewerComponent);
    fixture.componentInstance.type = 'mermaid';
    fixture.componentInstance.source = 'graph TD\\nA-->B';
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('graph TD');
  });
});

  it('neutralizes inline event handlers in markdown output', async () => {
    await TestBed.configureTestingModule({
      imports: [ControlCenterMarkdownMermaidViewerComponent],
    }).compileComponents();

    const fixture = TestBed.createComponent(ControlCenterMarkdownMermaidViewerComponent);
    fixture.componentInstance.type = 'markdown';
    fixture.componentInstance.source = '<img src=x onerror=alert(1) /><a href="#" onclick="evil()">x</a>';
    fixture.detectChanges();

    const html = fixture.nativeElement.innerHTML as string;
    expect(html.toLowerCase()).not.toContain('onerror');
    expect(html.toLowerCase()).not.toContain('onclick');
  });

