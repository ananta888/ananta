import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  inject,
  signal,
  computed,
} from '@angular/core';
import { RefactoringService } from '../services/refactoring.service';
import { PolicyService } from '../services/policy.service';
import {
  ChRefactorProposalReadModel,
  ChRefactorDiffReadModel,
  ChRefactorProposalInput,
  ChRefactorKind,
} from '../models/codehug.models';

/**
 * RefactoringPanelComponent — CH-005.
 *
 * Zeigt Vorschlaege an, kann Diff-Vorschau anfordern und Refactorings
 * anwenden. Apply erfordert aktiven write-Modus.
 *
 * SOLID: SRP — UI + Delegation an RefactoringService. Validierung im
 * Component-Layer (Write-Mode-Check) zur fruehen Rueckmeldung.
 */
@Component({
  selector: 'ch-refactoring-panel',
  standalone: true,
  imports: [],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ch-refactor">
      <header class="ch-refactor-head">
        <h4>Refactoring-Vorschlaege</h4>
        <p class="ch-muted">Deterministische + LLM-Vorschlaege. Apply erfordert Write-Modus.</p>
      </header>

      <form class="ch-refactor-form" (submit)="$event.preventDefault(); onPropose()">
        <label>
          Workspace-Pfad
          <input
            type="text"
            [value]="workspacePath()"
            (input)="workspacePath.set($any($event.target).value)"
            placeholder="/path/to/workspace" />
        </label>
        <label>
          Art
          <select [value]="kind()" (change)="kind.set($any($event.target).value)">
            <option value="auto">auto (alle)</option>
            <option value="rename_symbol">rename_symbol</option>
            <option value="extract_function">extract_function</option>
            <option value="inline_function">inline_function</option>
            <option value="move_to_module">move_to_module</option>
            <option value="convert_type">convert_type</option>
            <option value="wrap_with_helper">wrap_with_helper</option>
            <option value="optimize_imports">optimize_imports</option>
          </select>
        </label>
        <label>
          Symbol (optional)
          <input
            type="text"
            [value]="selectorSymbol()"
            (input)="selectorSymbol.set($any($event.target).value)"
            placeholder="symbolId" />
        </label>
        <button type="submit" class="ch-btn ch-btn-primary" [disabled]="proposing() || !workspacePath()">
          {{ proposing() ? 'ermittle…' : 'Vorschlaege ermitteln' }}
        </button>
      </form>

      @if (error(); as err) {
        <p class="ch-error">{{ err }}</p>
      }

      @if (proposals().length > 0) {
        <ul class="ch-refactor-list" data-testid="refactor-proposals">
          @for (p of proposals(); track p.id) {
            <li class="ch-refactor-item" [attr.data-status]="p.status" [attr.data-det]="p.generatedBy === 'deterministic' ? '1' : '0'">
              <header class="ch-refactor-item-head">
                <span class="ch-refactor-kind">{{ p.kind }}</span>
                <strong>{{ p.title }}</strong>
                <span class="ch-refactor-confidence" [attr.data-confidence]="p.confidence">
                  {{ (p.confidence * 100).toFixed(0) }}%
                </span>
                <span class="ch-refactor-src">{{ p.generatedBy }}</span>
              </header>
              <p class="ch-refactor-desc">{{ p.description }}</p>
              <p class="ch-refactor-files">
                <strong>{{ p.affectedFiles.length }}</strong> Dateien, <strong>{{ p.affectedSymbols.length }}</strong> Symbole
              </p>

              @if (selectedProposalId() === p.id && diff(); as d) {
                <div class="ch-refactor-diff">
                  @if (d.validation.syntaxOk && d.validation.typeCheckOk && d.validation.linterOk) {
                    <p class="ch-success">Syntaktisch und typ-validiert. Linter OK.</p>
                  } @else {
                    <p class="ch-warn">Validierungs-Warnungen:
                      @for (diag of d.validation.diagnostics; track $index) {
                        <span class="ch-diag" [attr.data-severity]="diag.severity">{{ diag.severity }}: {{ diag.message }}</span>
                      }
                    </p>
                  }
                  @for (h of d.hunks; track $index) {
                    <details>
                      <summary>{{ h.filePath }} ({{ h.oldLines }} → {{ h.newLines }})</summary>
                      <pre class="ch-unified">{{ h.unified }}</pre>
                    </details>
                  }
                </div>
              }

              <footer class="ch-refactor-actions">
                @if (selectedProposalId() !== p.id) {
                  <button type="button" class="ch-btn" (click)="onPreview(p)" [disabled]="diffLoading()">
                    {{ diffLoading() ? '…' : 'Diff anzeigen' }}
                  </button>
                } @else {
                  <button type="button" class="ch-btn" (click)="onPreview(p)">Diff aktualisieren</button>
                }
                <button
                  type="button"
                  class="ch-btn ch-btn-primary"
                  [disabled]="!policy.writeModeActive() || applying()"
                  [title]="policy.writeModeActive() ? '' : 'Write-Modus erforderlich'"
                  (click)="onApply(p)">
                  {{ applying() ? 'wende an…' : 'Anwenden' }}
                </button>
                <button
                  type="button"
                  class="ch-btn ch-btn-secondary"
                  [disabled]="dismissing()"
                  (click)="onDismiss(p)">
                  Verwerfen
                </button>
              </footer>

              @if (p.status === 'applied') {
                <p class="ch-success">Angewendet.</p>
              } @else if (p.status === 'dismissed') {
                <p class="ch-muted">Verworfen.</p>
              } @else if (p.status === 'failed') {
                <p class="ch-error">Anwendung fehlgeschlagen.</p>
              }
            </li>
          }
        </ul>
      } @else if (!proposing()) {
        <p class="ch-muted">Noch keine Vorschlaege ermittelt.</p>
      }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .ch-refactor { display: grid; gap: 10px; }
    .ch-refactor-head h4 { margin: 0; font-size: 14px; }
    .ch-muted { color: var(--muted); font-size: 12px; margin: 0; }
    .ch-warn { color: #92400e; font-size: 12px; }
    .ch-error { color: #b91c1c; font-size: 12px; margin: 0; }
    .ch-success { color: #065f46; font-size: 12px; margin: 0; }

    .ch-refactor-form {
      display: grid;
      grid-template-columns: 1fr max-content max-content max-content;
      gap: 8px;
      align-items: end;
    }
    .ch-refactor-form label { display: grid; gap: 3px; font-size: 11px; }
    .ch-refactor-form input, .ch-refactor-form select {
      padding: 4px 8px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--bg);
      color: var(--fg);
      font-size: 12px;
    }

    .ch-btn {
      padding: 4px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 12px;
    }
    .ch-btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    .ch-btn-secondary { background: var(--card-bg); }
    .ch-btn:disabled { opacity: 0.5; cursor: not-allowed; }

    .ch-refactor-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; }
    .ch-refactor-item {
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--card-bg);
    }
    .ch-refactor-item[data-det="1"] { background: color-mix(in srgb, #6b7280 8%, var(--card-bg)); }
    .ch-refactor-item[data-det="0"] { background: color-mix(in srgb, var(--accent) 6%, var(--card-bg)); }

    .ch-refactor-item-head { display: flex; gap: 8px; align-items: baseline; }
    .ch-refactor-kind {
      font-size: 10px;
      padding: 1px 6px;
      background: color-mix(in srgb, var(--accent) 20%, transparent);
      border-radius: 4px;
    }
    .ch-refactor-confidence {
      font-size: 10px;
      font-weight: 700;
    }
    .ch-refactor-confidence[data-confidence="0.9"] { color: #065f46; }
    .ch-refactor-confidence[data-confidence="0.6"] { color: #92400e; }
    .ch-refactor-src {
      font-size: 10px;
      padding: 1px 6px;
      background: var(--bg);
      border-radius: 4px;
    }
    .ch-refactor-desc { margin: 4px 0; font-size: 12px; }
    .ch-refactor-files { font-size: 11px; color: var(--muted); margin: 4px 0; }

    .ch-refactor-diff {
      margin: 6px 0;
      padding: 6px;
      background: var(--bg);
      border-radius: 4px;
    }
    .ch-refactor-diff details { margin: 4px 0; }
    .ch-unified {
      margin: 0;
      padding: 6px 8px;
      background: var(--bg);
      border-radius: 4px;
      max-height: 240px;
      overflow: auto;
      font-size: 11px;
      font-family: var(--mono, ui-monospace, monospace);
    }
    .ch-diag { display: inline-block; margin: 2px 4px 2px 0; padding: 1px 6px; border-radius: 3px; font-size: 10px; }
    .ch-diag[data-severity="error"] { background: color-mix(in srgb, #ef4444 20%, transparent); }
    .ch-diag[data-severity="warning"] { background: color-mix(in srgb, #f59e0b 20%, transparent); }

    .ch-refactor-actions { display: flex; gap: 4px; margin-top: 6px; }
  `]
})
export class RefactoringPanelComponent implements OnChanges {
  @Input() initialWorkspacePath = '';
  @Output() proposalApplied = new EventEmitter<ChRefactorProposalReadModel>();

  readonly svc = inject(RefactoringService);
  readonly policy = inject(PolicyService);

  readonly workspacePath = signal('');
  readonly kind = signal<ChRefactorKind | 'auto'>('auto');
  readonly selectorSymbol = signal('');

  readonly proposals = signal<ChRefactorProposalReadModel[]>([]);
  readonly diff = signal<ChRefactorDiffReadModel | null>(null);
  readonly selectedProposalId = signal<string | null>(null);

  readonly proposing = signal(false);
  readonly diffLoading = signal(false);
  readonly applying = signal(false);
  readonly dismissing = signal(false);
  readonly error = signal<string | null>(null);

  readonly hasDet = computed(() => this.proposals().filter(p => p.generatedBy === 'deterministic').length);
  readonly hasLlm = computed(() => this.proposals().filter(p => p.generatedBy === 'llm').length);

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['initialWorkspacePath'] && this.initialWorkspacePath) {
      this.workspacePath.set(this.initialWorkspacePath);
    }
  }

  onPropose(): void {
    if (!this.workspacePath()) return;
    this.proposing.set(true);
    this.error.set(null);
    const input: ChRefactorProposalInput = {
      workspacePath: this.workspacePath(),
      kind: this.kind(),
      selector: this.selectorSymbol() ? { symbolId: this.selectorSymbol() } : undefined,
    };
    this.svc.propose(input).subscribe({
      next: list => {
        this.proposals.set(list);
        this.proposing.set(false);
        this.selectedProposalId.set(null);
        this.diff.set(null);
      },
      error: err => {
        this.error.set(err.message ?? 'Vorschlaege konnten nicht ermittelt werden');
        this.proposing.set(false);
      },
    });
  }

  onPreview(p: ChRefactorProposalReadModel): void {
    this.selectedProposalId.set(p.id);
    this.diffLoading.set(true);
    this.svc.previewDiff(p.id).subscribe({
      next: d => {
        this.diff.set(d);
        this.diffLoading.set(false);
        this.proposals.update(list => list.map(x => x.id === p.id ? { ...x, status: 'previewed' } : x));
      },
      error: err => {
        this.error.set(err.message ?? 'Diff konnte nicht geladen werden');
        this.diffLoading.set(false);
      },
    });
  }

  onApply(p: ChRefactorProposalReadModel): void {
    if (!this.policy.writeModeActive()) {
      this.error.set('Write-Modus nicht aktiv.');
      return;
    }
    this.applying.set(true);
    this.error.set(null);
    this.svc.apply(p.id).subscribe({
      next: result => {
        this.applying.set(false);
        this.proposals.update(list => list.map(x => x.id === p.id ? { ...x, status: result.status as any } : x));
        this.proposalApplied.emit({ ...p, status: result.status as any });
      },
      error: err => {
        this.error.set(err.message ?? 'Apply fehlgeschlagen');
        this.applying.set(false);
        this.proposals.update(list => list.map(x => x.id === p.id ? { ...x, status: 'failed' } : x));
      },
    });
  }

  onDismiss(p: ChRefactorProposalReadModel): void {
    this.dismissing.set(true);
    this.svc.dismiss(p.id).subscribe({
      next: () => {
        this.dismissing.set(false);
        this.proposals.update(list => list.filter(x => x.id !== p.id));
      },
      error: err => {
        this.error.set(err.message ?? 'Verwerfen fehlgeschlagen');
        this.dismissing.set(false);
      },
    });
  }
}