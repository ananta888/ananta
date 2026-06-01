import { Component, Input, inject } from '@angular/core';
import { NgFor, NgIf } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

export type CcArtifactType = 'markdown' | 'mermaid' | 'diff' | 'json' | 'log' | 'text';
export interface CcArtifact {
  id: string;
  title: string;
  type: CcArtifactType;
  content: string;
}

@Component({
  standalone: true,
  selector: 'app-control-center-markdown-mermaid-viewer',
  imports: [NgIf],
  template: `
    <div *ngIf="type==='markdown'" class="viewer prose" [innerHTML]="safeMarkdown"></div>
    <div *ngIf="type==='mermaid'" class="viewer mermaid-box">
      <pre>{{ mermaidSource }}</pre>
      <p class="muted">Mermaid-Preview-Fallback: bei Renderfehler bleibt nur der Quelltext sichtbar.</p>
    </div>
  `,
  styles: [`.viewer{border:1px solid #1f2937;border-radius:8px;padding:10px;background:#0f172a}.prose :where(h1,h2,h3){color:#e5e7eb}.prose{color:#d1d5db}.mermaid-box pre{white-space:pre-wrap}.muted{color:#94a3b8;font-size:12px}`]
})
export class ControlCenterMarkdownMermaidViewerComponent {
  @Input() type: 'markdown' | 'mermaid' = 'markdown';
  @Input() source = '';
  private sanitizer = inject(DomSanitizer);

  get safeMarkdown(): SafeHtml {
    const html = marked.parse(this.source || '') as string;
    const sanitized = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
    return this.sanitizer.bypassSecurityTrustHtml(sanitized);
  }

  get mermaidSource(): string { return this.source || ''; }
}

@Component({
  standalone: true,
  selector: 'app-control-center-diff-viewer',
  template: `
    <div class="switcher">
      <button (click)="mode='unified'" [class.active]="mode==='unified'">Unified</button>
      <button (click)="mode='split'" [class.active]="mode==='split'">Side-by-side</button>
    </div>
    <pre class="diff" *ngIf="mode==='unified'">{{ diff }}</pre>
    <div class="split" *ngIf="mode==='split'">
      <pre>{{ left }}</pre>
      <pre>{{ right }}</pre>
    </div>
  `,
  styles: [`.switcher{display:flex;gap:8px;margin-bottom:8px}.switcher button.active{font-weight:700}.diff,.split pre{border:1px solid #1f2937;border-radius:8px;padding:8px;background:#111827;white-space:pre-wrap}.split{display:grid;grid-template-columns:1fr 1fr;gap:8px}@media (max-width:900px){.split{grid-template-columns:1fr}}`]
})
export class ControlCenterDiffViewerComponent {
  @Input() diff = '';
  mode: 'unified' | 'split' = 'unified';
  get left(): string { return this.diff.split('\n').filter(l => !l.startsWith('+')).join('\n'); }
  get right(): string { return this.diff.split('\n').filter(l => !l.startsWith('-')).join('\n'); }
}

@Component({
  standalone: true,
  selector: 'app-control-center-artifact-browser',
  imports: [NgFor, NgIf, ControlCenterMarkdownMermaidViewerComponent, ControlCenterDiffViewerComponent],
  template: `
    <h2>Artifacts</h2>
    <div class="grid">
      <aside class="list">
        <button *ngFor="let a of artifacts" (click)="select(a.id)" [class.active]="a.id===selectedId">{{ a.title }} <small>({{ a.type }})</small></button>
      </aside>
      <section class="view" *ngIf="selected as a">
        <h3>{{ a.title }}</h3>
        <app-control-center-markdown-mermaid-viewer *ngIf="a.type==='markdown' || a.type==='mermaid'" [type]="a.type" [source]="a.content" />
        <app-control-center-diff-viewer *ngIf="a.type==='diff'" [diff]="a.content" />
        <pre *ngIf="a.type==='json' || a.type==='log' || a.type==='text'" class="raw">{{ a.content }}</pre>
      </section>
    </div>
  `,
  styles: [`.grid{display:grid;grid-template-columns:280px 1fr;gap:10px}.list{display:flex;flex-direction:column;gap:6px}.list button{border:1px solid #1f2937;background:#0f172a;color:#e5e7eb;border-radius:8px;padding:8px;text-align:left}.list button.active{border-color:#2563eb}.raw{border:1px solid #1f2937;border-radius:8px;padding:10px;background:#111827;white-space:pre-wrap}@media (max-width:900px){.grid{grid-template-columns:1fr}}`]
})
export class ControlCenterArtifactBrowserComponent {
  artifacts: CcArtifact[] = [
    { id:'a1', title:'Plan', type:'markdown', content:'# Plan\n- Session prüfen\n- Policies anzeigen' },
    { id:'a2', title:'Flow', type:'mermaid', content:'graph TD\nA[Task]-->B[Session]\nB-->C[Artifacts]' },
    { id:'a3', title:'Patch', type:'diff', content:'--- a/file.ts\n+++ b/file.ts\n@@\n-const old=true;\n+const old=false;' },
    { id:'a4', title:'Verification JSON', type:'json', content:'{"status":"passed","tests":18}' },
  ];
  selectedId = 'a1';
  select(id: string): void { this.selectedId = id; }
  get selected(): CcArtifact | undefined { return this.artifacts.find(a => a.id === this.selectedId); }
}
