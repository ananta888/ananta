import {
  Component, Input, Output, EventEmitter, AfterViewInit, ViewChild, ElementRef,
  OnDestroy, inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
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

function normalizeMermaidForRetry(code: string): string {
  return code
    .split('\n')
    .map(line => {
      let trimmed = line.trimEnd();
      if (trimmed.endsWith(';') && !trimmed.includes('"')) {
        trimmed = trimmed.slice(0, -1);
      }
      return trimmed;
    })
    .join('\n')
    .trim();
}

@Component({
  standalone: true,
  selector: 'app-mermaid-diagram',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="diagram-wrap" (click)="onWrapClick()">
      <div #host class="diagram-host"></div>

      @if (!editing && !done && !err) {
        <div class="diagram-loading">Rendering…</div>
      }

      @if (!editing && done) {
        <span class="expand-hint">⤢</span>
      }

      @if (!editing && err) {
        <div class="diagram-err" (click)="$event.stopPropagation()">
          <div class="err-head">
            <strong>Mermaid Render-Fehler</strong>
            <button class="btn-mini" (click)="showRaw = !showRaw">
              {{ showRaw ? '▲' : '▼' }} Rohcode
            </button>
          </div>
          <div class="err-msg">{{ err }}</div>
          <div class="err-hints">
            Typische Fixes: Node-Labels quoten, Edge-Labels quoten, Semikolons entfernen,
            keine Sonderzeichen in Node-IDs.
          </div>
          <div class="err-actions">
            <button class="btn-mini" (click)="copyCode()">📋 Code</button>
            <button class="btn-mini" (click)="copyError()">📋 Fehler</button>
            <button class="btn-mini" (click)="copyBoth()">📋 Fehler + Code</button>
            <button class="btn-mini" (click)="startEditing()">✎ Bearbeiten</button>
            <button class="btn-mini" (click)="retryToChat()">💬 Reparatur</button>
            <button class="btn-mini" (click)="normalizeAndRetry()">✨ Sanft bereinigen</button>
          </div>
          @if (showRaw) {
            <pre class="raw-code">{{ code }}</pre>
          }
        </div>
      }

      @if (editing) {
        <div class="edit-area" (click)="$event.stopPropagation()">
          <div class="edit-hint">Mermaid-Code bearbeiten:</div>
          <textarea class="edit-textarea" [(ngModel)]="editCode" rows="6"></textarea>
          <div class="edit-actions">
            <button class="btn-mini" (click)="doRender()">🔄 Neu rendern</button>
            <button class="btn-mini" (click)="resetEdit()">↩ Zurücksetzen</button>
            <button class="btn-mini" (click)="copyEditCode()">📋 Kopieren</button>
            <button class="btn-mini" (click)="cancelEditing()">Abbrechen</button>
          </div>
        </div>
      }
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
      position: relative; margin: 6px 0;
      background: #0a1a2e; border: 1px solid #1a3050; border-radius: 4px;
      padding: 8px; display: inline-block; max-width: 100%;
    }
    .diagram-wrap:not(.editing) { cursor: zoom-in; }
    .diagram-wrap:hover { border-color: #2a5090; }
    .diagram-host { display: block; }
    .diagram-host svg { max-width: 100%; height: auto; display: block; }
    .diagram-loading { color: #4a6a9a; font-size: 11px; padding: 4px; }
    .expand-hint {
      position: absolute; top: 4px; right: 6px;
      color: #2a5090; font-size: 13px; pointer-events: none;
    }
    .diagram-wrap:hover .expand-hint { color: #7fffd4; }

    /* Error */
    .diagram-err {
      color: #fb7185; font-size: 11px; padding: 4px;
    }
    .err-head {
      display: flex; align-items: center; gap: 8px; margin-bottom: 4px;
    }
    .err-msg {
      background: #1a0a0a; border: 1px solid #3a1a1a; border-radius: 3px;
      padding: 4px 6px; font-family: monospace; font-size: 10px;
      white-space: pre-wrap; word-break: break-word; margin-bottom: 4px;
    }
    .err-hints {
      color: #9a7a3a; font-size: 10px; margin-bottom: 4px; line-height: 1.4;
    }
    .err-actions {
      display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 4px;
    }

    /* Mini buttons */
    .btn-mini {
      background: transparent; border: 1px solid #3a2a4a; color: #a8b8d8;
      padding: 2px 7px; font-size: 10px; border-radius: 3px; cursor: pointer;
      font-family: inherit; white-space: nowrap;
    }
    .btn-mini:hover { background: #1a1a30; border-color: #4a5a8a; }

    /* Raw code */
    .raw-code {
      margin: 6px 0 0; padding: 6px; background: #0a0f1a;
      border: 1px solid #1a1a30; border-radius: 3px;
      font-size: 11px; font-family: monospace; white-space: pre;
      overflow-x: auto; max-height: 200px; color: #6a8ab8;
    }

    /* Edit area */
    .edit-area {
      padding: 4px 0;
    }
    .edit-hint {
      font-size: 10px; color: #6a8ab8; margin-bottom: 4px;
    }
    .edit-textarea {
      width: 100%; box-sizing: border-box;
      background: #0a0f1a; border: 1px solid #2a4070; color: #c8d8f8;
      padding: 6px; font-size: 11px; font-family: monospace;
      border-radius: 3px; resize: vertical; min-height: 60px;
    }
    .edit-actions {
      display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px;
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
  @Output() retryRequest = new EventEmitter<string>();

  @ViewChild('host') host!: ElementRef<HTMLDivElement>;

  done = false;
  err = '';
  lightbox = false;
  showRaw = false;
  safeSvg: SafeHtml = '';

  editing = false;
  editCode = '';
  originalCode = '';

  private sanitizer = inject(DomSanitizer);
  private readonly id = `mermaid-d${++_counter}-${Math.random().toString(36).slice(2, 7)}`;

  async ngAfterViewInit(): Promise<void> {
    await this._render(this.code.trim());
  }

  ngOnDestroy(): void {
    document.getElementById(this.id)?.remove();
  }

  onWrapClick(): void {
    if (this.done) this.lightbox = true;
  }

  // ── Rendering ──

  private async _render(input: string): Promise<void> {
    this.done = false;
    this.err = '';
    this.editing = false;
    try {
      await ensureMermaid();
      const mermaidModule = await import('mermaid');
      const mermaid = mermaidModule.default;
      const { svg } = await mermaid.render(this.id, input);
      this.host.nativeElement.innerHTML = svg;
      this.safeSvg = this.sanitizer.bypassSecurityTrustHtml(svg);
      this.done = true;
    } catch (e: any) {
      this.err = String(e?.message ?? 'Render-Fehler');
      document.getElementById(this.id)?.remove();
    }
  }

  async doRender(): Promise<void> {
    const trimmed = this.editCode.trim();
    if (!trimmed) return;
    this.originalCode = this.code;
    await this._render(trimmed);
    if (this.done) {
      this.code = trimmed;
      this.editCode = trimmed;
    }
  }

  // ── Edit mode ──

  startEditing(): void {
    this.editing = true;
    this.editCode = this.code;
    this.originalCode = this.code;
  }

  cancelEditing(): void {
    this.editing = false;
    if (!this.done) {
      this.code = this.originalCode;
      this._render(this.originalCode);
    }
  }

  resetEdit(): void {
    this.editCode = this.originalCode;
  }

  // ── Normalize ──

  normalizeAndRetry(): void {
    const normalized = normalizeMermaidForRetry(this.code);
    if (normalized !== this.code) {
      this.code = normalized;
      this._render(normalized);
    } else {
      this._render(this.code);
    }
  }

  // ── Copy ──

  copyCode(): void {
    navigator.clipboard.writeText(this.code).catch(() => {});
  }

  copyError(): void {
    navigator.clipboard.writeText(this.err).catch(() => {});
  }

  copyBoth(): void {
    navigator.clipboard.writeText(`Fehler:\n${this.err}\n\nCode:\n${this.code}`).catch(() => {});
  }

  copyEditCode(): void {
    navigator.clipboard.writeText(this.editCode).catch(() => {});
  }

  // ── Close lightbox ──

  close(): void {
    this.lightbox = false;
  }

  // ── Retry to chat ──

  retryToChat(): void {
    this.retryRequest.emit(this.code);
  }
}
