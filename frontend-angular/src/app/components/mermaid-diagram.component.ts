import {
  Component, Input, AfterViewInit, ViewChild, ElementRef,
  OnDestroy, inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';

let _ready = false;
let _initPromise: Promise<void> | null = null;
let _counter = 0;

async function ensureMermaid(): Promise<void> {
  if (_ready) return;
  if (_initPromise) return _initPromise;
  _initPromise = import('mermaid').then(m => {
    m.default.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose' });
    _ready = true;
  });
  return _initPromise;
}

@Component({
  standalone: true,
  selector: 'app-mermaid-diagram',
  imports: [CommonModule],
  template: `
    <div class="diagram-wrap" (click)="open()" title="Klicken zum Vergrößern">
      <div #host class="diagram-host"></div>
      @if (err) {
        <div class="diagram-err">
          <strong>Mermaid Render-Fehler:</strong> {{ err }}
          <button class="raw-toggle" (click)="showRaw = !showRaw; $event.stopPropagation()">
            {{ showRaw ? '▲ Rohtext' : '▼ Rohtext' }}
          </button>
          @if (showRaw) {
            <pre class="raw-code">{{ code }}</pre>
          }
        </div>
      }
      @if (!done && !err) { <div class="diagram-loading">Rendering…</div> }
      @if (done) { <span class="expand-hint">⤢</span> }
    </div>

    @if (lightbox) {
      <div class="lb-backdrop" (click)="close()">
        <button class="lb-close" (click)="close()">✕</button>
        <div class="lb-box" (click)="$event.stopPropagation()" [innerHTML]="safeSvg"></div>
      </div>
    }
  `,
  styles: [`
    .diagram-wrap {
      position: relative; cursor: zoom-in; margin: 6px 0;
      background: #0a1a2e; border: 1px solid #1a3050; border-radius: 4px;
      padding: 8px; display: inline-block; max-width: 100%;
    }
    .diagram-wrap:hover { border-color: #2a5090; }
    .diagram-host { display: block; }
    .diagram-host svg { max-width: 100%; height: auto; display: block; }
    .diagram-err { color: #fb7185; font-size: 11px; padding: 4px; }
    .diagram-loading { color: #4a6a9a; font-size: 11px; padding: 4px; }
    .expand-hint {
      position: absolute; top: 4px; right: 6px;
      color: #2a5090; font-size: 13px; pointer-events: none;
    }
    .diagram-wrap:hover .expand-hint { color: #7fffd4; }

    .raw-toggle {
      background: transparent; border: 1px solid #4a1a1a; color: #fb7185;
      padding: 1px 6px; font-size: 10px; border-radius: 2px; cursor: pointer;
      margin-left: 6px; font-family: inherit;
    }
    .raw-toggle:hover { background: #1a0a0a; }
    .raw-code {
      margin: 6px 0 0; padding: 6px; background: #0a0f1a;
      border: 1px solid #1a1a30; border-radius: 3px;
      font-size: 11px; font-family: monospace; white-space: pre;
      overflow-x: auto; max-height: 200px; color: #6a8ab8;
    }

    /* Lightbox */
    .lb-backdrop {
      position: fixed; inset: 0; z-index: 9999;
      background: rgba(5, 12, 25, 0.92);
      display: flex; align-items: center; justify-content: center;
    }
    .lb-close {
      position: fixed; top: 18px; right: 24px; z-index: 10000;
      background: #0f1c30; border: 1px solid #2a4070; color: #c8d8f8;
      font-size: 20px; width: 36px; height: 36px; border-radius: 4px;
      cursor: pointer; line-height: 1; display: flex; align-items: center; justify-content: center;
    }
    .lb-close:hover { background: #1a3050; color: #7fffd4; }
    .lb-box {
      max-width: 92vw; max-height: 90vh; overflow: auto;
      background: #0a1525; border: 1px solid #1a3050; border-radius: 6px; padding: 24px;
    }
    .lb-box svg { max-width: 100%; height: auto; display: block; }
  `],
})
export class MermaidDiagramComponent implements AfterViewInit, OnDestroy {
  @Input() code = '';
  @ViewChild('host') host!: ElementRef<HTMLDivElement>;

  done = false;
  err = '';
  lightbox = false;
  showRaw = false;
  safeSvg: SafeHtml = '';

  private sanitizer = inject(DomSanitizer);
  private readonly id = `mermaid-d${++_counter}-${Math.random().toString(36).slice(2, 7)}`;

  async ngAfterViewInit(): Promise<void> {
    try {
      await ensureMermaid();
      const mermaid = (await import('mermaid')).default;
      const { svg } = await mermaid.render(this.id, this.code.trim());
      this.host.nativeElement.innerHTML = svg;
      this.safeSvg = this.sanitizer.bypassSecurityTrustHtml(svg);
      this.done = true;
    } catch (e: any) {
      this.err = String(e?.message ?? 'Render-Fehler');
      // remove leftover mermaid error element mermaid may have inserted
      document.getElementById(this.id)?.remove();
    }
  }

  ngOnDestroy(): void {
    document.getElementById(this.id)?.remove();
  }

  open(): void { if (this.done) this.lightbox = true; }
  close(): void { this.lightbox = false; }
}
