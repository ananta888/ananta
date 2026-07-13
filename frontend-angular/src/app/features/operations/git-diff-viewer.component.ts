import { Component, EventEmitter, Input, Output } from '@angular/core';
import { RouterLink } from '@angular/router';

interface RenderedDiffLine { number: number; text: string; kind: 'add' | 'delete' | 'hunk' | 'meta' | 'context'; }

@Component({
  selector: 'app-git-diff-viewer',
  standalone: true,
  imports: [RouterLink],
  template: `
    <section class="diff-viewer">
      <div class="diff-toolbar">
        <div>
          <strong>{{ path || 'Gesamter Workspace' }}</strong>
          <span class="muted"> · {{ scopeLabel() }}</span>
          @if (truncated) { <span class="warning"> · gekürzt</span> }
        </div>
        <div class="row">
          <button type="button" class="secondary btn-small" [class.active]="scope === 'unstaged'" (click)="modeChange.emit('unstaged')">Arbeitskopie</button>
          <button type="button" class="secondary btn-small" [class.active]="scope === 'staged'" (click)="modeChange.emit('staged')">Staging</button>
          <button type="button" class="secondary btn-small" [class.active]="scope === 'combined'" (click)="modeChange.emit('combined')">Kombiniert</button>
          <a class="button-link" [routerLink]="['/diff3']" [queryParams]="{ workspace: workspaceId, file: path || undefined, preset: 'git' }">Im 3er-Diff öffnen</a>
          <button type="button" class="secondary btn-small" (click)="close.emit()">Schließen</button>
        </div>
      </div>
      @if (loading) { <div class="diff-empty">Diff wird geladen…</div> }
      @if (error) { <div class="state-banner error">{{ error }}</div> }
      @if (!loading && !error && !diff) { <div class="diff-empty">In diesem Bereich gibt es für die Auswahl keinen Diff.</div> }
      @if (!loading && diff) {
        <div class="diff-content" tabindex="0" aria-label="Git Diff">
          @for (line of lines(); track line.number) {
            <div class="diff-line" [class]="'diff-line ' + line.kind">
              <span class="line-number">{{ line.number }}</span><span class="line-text">{{ line.text || ' ' }}</span>
            </div>
          }
        </div>
      }
    </section>
  `,
  styles: [`
    .diff-viewer { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; background: var(--surface-raised); }
    .diff-toolbar { display: flex; justify-content: space-between; gap: 10px; align-items: center; flex-wrap: wrap; padding: 9px 12px; border-bottom: 1px solid var(--border); }
    .secondary.active { border-color: var(--accent); color: var(--accent); }
    .button-link { display: inline-flex; align-items: center; padding: 5px 8px; border: 1px solid var(--accent); border-radius: 6px; font-size: 12px; }
    .diff-content { max-height: 520px; overflow: auto; background: #0b1220; color: #dbeafe; font: 12px/1.48 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .diff-line { display: grid; grid-template-columns: 54px minmax(max-content, 1fr); min-height: 18px; }
    .diff-line.add { background: rgba(34, 197, 94, .17); color: #bbf7d0; }
    .diff-line.delete { background: rgba(239, 68, 68, .16); color: #fecaca; }
    .diff-line.hunk { background: rgba(14, 165, 233, .18); color: #bae6fd; }
    .diff-line.meta { color: #c4b5fd; }
    .line-number { position: sticky; left: 0; user-select: none; text-align: right; padding: 0 9px; color: #64748b; border-right: 1px solid #253147; background: inherit; }
    .line-text { white-space: pre; padding: 0 10px; }
    .diff-empty { padding: 28px; text-align: center; color: var(--muted); }
  `],
})
export class GitDiffViewerComponent {
  @Input() workspaceId = 'repo';
  @Input() path = '';
  @Input() scope: 'unstaged' | 'staged' | 'combined' = 'unstaged';
  @Input() diff = '';
  @Input() truncated = false;
  @Input() loading = false;
  @Input() error = '';
  @Output() modeChange = new EventEmitter<'unstaged' | 'staged' | 'combined'>();
  @Output() close = new EventEmitter<void>();

  lines(): RenderedDiffLine[] {
    return (this.diff || '').split('\n').map((text, index) => ({
      number: index + 1,
      text,
      kind: text.startsWith('+++') || text.startsWith('---') || text.startsWith('diff ') || text.startsWith('index ')
        ? 'meta'
        : text.startsWith('@@') ? 'hunk' : text.startsWith('+') ? 'add' : text.startsWith('-') ? 'delete' : 'context',
    }));
  }

  scopeLabel(): string {
    return this.scope === 'staged' ? 'Staging Area' : this.scope === 'combined' ? 'Arbeitskopie + Staging Area' : 'Arbeitskopie';
  }
}
