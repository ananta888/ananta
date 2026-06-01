import { Component, Input, OnDestroy, OnInit, inject } from '@angular/core';
import { NgFor, NgIf } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { FormsModule } from '@angular/forms';
import { Subject, takeUntil } from 'rxjs';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { HubControlCenterApiClient } from '../services/hub-control-center-api.client';

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
    const html = marked.parse(this.source || '', { breaks: true }) as string;
    const sanitized = DOMPurify.sanitize(html, {
      USE_PROFILES: { html: true },
      FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed'],
      FORBID_ATTR: ['onerror', 'onload', 'onclick', 'style'],
    });
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
  imports: [NgFor, NgIf, FormsModule, ControlCenterMarkdownMermaidViewerComponent, ControlCenterDiffViewerComponent],
  template: `
    <h2>Artifacts</h2>
    <div class="filters">
      <label>Project <input [(ngModel)]="selectedProject" placeholder="project id" /></label>
      <label>Task <input [(ngModel)]="selectedTask" placeholder="task id" /></label>
      <label>Session <input [(ngModel)]="selectedSession" placeholder="session id" /></label>
      <label>Type
        <select [(ngModel)]="selectedType">
          <option value="all">all</option>
          <option value="markdown">markdown</option>
          <option value="mermaid">mermaid</option>
          <option value="diff">diff</option>
          <option value="json">json</option>
          <option value="log">log</option>
          <option value="text">text</option>
        </select>
      </label>
      <button type="button" (click)="loadArtifacts()">Reload</button>
    </div>
    <div class="grid">
      <aside class="list">
        <button *ngFor="let a of filteredArtifacts" (click)="select(a.id)" [class.active]="a.id===selectedId">{{ a.title }} <small>({{ a.type }})</small></button>
      </aside>
      <section class="view" *ngIf="selected as a">
        <h3>{{ a.title }}</h3>
        <app-control-center-markdown-mermaid-viewer *ngIf="a.type==='markdown' || a.type==='mermaid'" [type]="a.type" [source]="a.content" />
        <app-control-center-diff-viewer *ngIf="a.type==='diff'" [diff]="a.content" />
        <pre *ngIf="a.type==='json' || a.type==='log' || a.type==='text'" class="raw">{{ a.content }}</pre>
      </section>
    </div>
  `,
  styles: [`.filters{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:8px;margin-bottom:10px}.filters input,.filters select{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px}.grid{display:grid;grid-template-columns:280px 1fr;gap:10px}.list{display:flex;flex-direction:column;gap:6px}.list button{border:1px solid #1f2937;background:#0f172a;color:#e5e7eb;border-radius:8px;padding:8px;text-align:left}.list button.active{border-color:#2563eb}.raw{border:1px solid #1f2937;border-radius:8px;padding:10px;background:#111827;white-space:pre-wrap}@media (max-width:900px){.filters{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}}`]
})
export class ControlCenterArtifactBrowserComponent implements OnInit {
  private state = inject(ControlCenterStateFacade);
  private api = inject(HubControlCenterApiClient);
  artifacts: CcArtifact[] = [];
  selectedId = '';
  selectedProject = '';
  selectedTask = '';
  selectedSession = '';
  selectedType: 'all' | CcArtifactType = 'all';
  loading = false;
  private readonly destroy$ = new Subject<void>();
  private readonly loadedContentIds = new Set<string>();

  ngOnInit(): void {
    this.state.projects$.pipe(takeUntil(this.destroy$)).subscribe((rows) => {
      if (!this.selectedProject && rows.length) this.selectedProject = rows[0].id;
    });
    this.state.loadProjects();
    this.loadArtifacts();
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  loadArtifacts(): void {
    const base = this.state.hubBaseUrl();
    if (!base) return;
    this.loading = true;
    this.api.listArtifacts(base).subscribe({
      next: (rows) => {
        this.artifacts = rows.map((row) => ({
          id: row.id,
          title: row.latest_filename || row.id,
          type: this.mapType(String(row.latest_media_type || 'text/plain')),
          content: '',
        }));
        this.loadedContentIds.clear();
        this.selectedId = this.artifacts[0]?.id || '';
        if (this.selectedId) this.loadContent(this.selectedId);
      },
      error: () => {
        this.artifacts = [];
      },
      complete: () => {
        this.loading = false;
      },
    });
  }

  loadContent(id: string): void {
    if (this.loadedContentIds.has(id)) return;
    const base = this.state.hubBaseUrl();
    if (!base) return;
    this.loadedContentIds.add(id);
    this.api.getArtifactContentNormalized(base, id).subscribe({
      next: (content) => {
        const idx = this.artifacts.findIndex((a) => a.id === id);
        if (idx < 0) return;
        const decoded = content.encoding === 'base64'
          ? atob(content.payload || '')
          : String(content.payload || '');
        this.artifacts[idx] = { ...this.artifacts[idx], content: decoded };
      },
      error: () => {
        this.loadedContentIds.delete(id);
      },
    });
  }

  select(id: string): void {
    this.selectedId = id;
    const selected = this.artifacts.find((a) => a.id === id);
    if (selected && !selected.content) this.loadContent(id);
  }
  get selected(): CcArtifact | undefined {
    return this.filteredArtifacts.find((a) => a.id === this.selectedId) || this.filteredArtifacts[0];
  }

  get filteredArtifacts(): CcArtifact[] {
    const projectQ = this.selectedProject.trim().toLowerCase();
    const taskQ = this.selectedTask.trim().toLowerCase();
    const sessionQ = this.selectedSession.trim().toLowerCase();
    return this.artifacts.filter((a) => {
      const typeOk = this.selectedType === 'all' || a.type === this.selectedType;
      const hay = `${a.id} ${a.title}`.toLowerCase();
      const projectOk = !projectQ || hay.includes(projectQ);
      const taskOk = !taskQ || hay.includes(taskQ);
      const sessionOk = !sessionQ || hay.includes(sessionQ);
      return typeOk && projectOk && taskOk && sessionOk;
    });
  }

  private mapType(mediaType: string): CcArtifactType {
    const m = mediaType.toLowerCase();
    if (m.includes('markdown')) return 'markdown';
    if (m.includes('mermaid')) return 'mermaid';
    if (m.includes('diff') || m.includes('patch')) return 'diff';
    if (m.includes('json')) return 'json';
    if (m.includes('log')) return 'log';
    return 'text';
  }
}
