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
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { CustomAgentService } from '../services/custom-agent.service';
import { PolicyService } from '../services/policy.service';
import {
  ChAgentDefinitionReadModel,
  ChAgentDefinitionInput,
  ChAgentRunReadModel,
} from '../models/codehug.models';

/**
 * CustomAgentEditorComponent — CH-006.
 *
 * Liste + Editor fuer Custom Agent-Definitionen. Run-Action startet einen
 * Agent-Run (analog zu features/codehug/services/agent-run.service.ts).
 *
 * SOLID: SRP — UI und Delegation. Validierung des Inputs (name + systemPrompt
 * nicht leer) im Component-Layer.
 */
@Component({
  selector: 'ch-custom-agent-editor',
  standalone: true,
  imports: [DatePipe, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ch-agent">
      <header class="ch-agent-head">
        <h4>Custom Agent-Definitionen</h4>
        <p class="ch-muted">{{ agents().length }} Agent(s) konfiguriert.</p>
        <button type="button" class="ch-btn ch-btn-primary" (click)="onStartCreate()">
          + Neuer Agent
        </button>
      </header>

      @if (error(); as err) {
        <p class="ch-error">{{ err }}</p>
      }

      <ul class="ch-agent-list">
        @for (a of agents(); track a.id) {
          <li class="ch-agent-item" [class.ch-agent-selected]="selectedId() === a.id">
            <header class="ch-agent-item-head">
              <strong>{{ a.name }}</strong>
              <span class="ch-agent-id">{{ a.id }}</span>
              <button type="button" class="ch-btn" (click)="selectedId.set(a.id); loadForEdit(a.id)">Bearbeiten</button>
              <button type="button" class="ch-btn ch-btn-secondary"
                [disabled]="!policy.writeModeActive()"
                (click)="onRemove(a)">Loeschen</button>
            </header>
            <p class="ch-agent-desc">{{ a.description }}</p>
            <p class="ch-agent-meta">
              Backend: <strong>{{ a.preferredBackend ?? 'auto' }}</strong>,
              Modell: <strong>{{ a.preferredModel ?? 'auto' }}</strong>,
              {{ a.runCount }} Runs,
              zuletzt: {{ a.lastRunAt ? (a.lastRunAt | date: 'short') : 'nie' }}
            </p>
            <p class="ch-agent-tags">
              @for (t of a.tags ?? []; track t) { <span class="ch-tag">{{ t }}</span> }
              @for (c of a.capabilities ?? []; track c) { <span class="ch-cap">{{ c }}</span> }
            </p>
            <button type="button" class="ch-btn ch-btn-primary" (click)="onRunStart(a)">Run starten</button>

            @if (selectedId() === a.id && editing()) {
              <form class="ch-agent-form" (submit)="$event.preventDefault(); onSave()">
                <label>Name <input type="text" [(ngModel)]="form().name" name="name" required /></label>
                <label>Beschreibung <input type="text" [(ngModel)]="form().description" name="description" required /></label>
                <label>System-Prompt
                  <textarea [(ngModel)]="form().systemPrompt" name="systemPrompt" rows="6" required></textarea>
                </label>
                <label>Bevorzugtes Backend
                  <select [(ngModel)]="form().preferredBackend" name="preferredBackend">
                    <option [ngValue]="undefined">auto</option>
                    <option value="sgpt">sgpt</option>
                    <option value="opencode">opencode</option>
                    <option value="codex">codex</option>
                    <option value="claude_code">claude_code</option>
                    <option value="aider">aider</option>
                    <option value="mistral">mistral</option>
                  </select>
                </label>
                <label>Bevorzugtes Modell <input type="text" [(ngModel)]="form().preferredModel" name="preferredModel" /></label>
                <label>Tags (Komma-getrennt) <input type="text" [value]="tagsCsv()" (input)="tagsCsv.set($any($event.target).value)" /></label>
                <label>Capabilities (Komma-getrennt)
                  <input type="text" [value]="capsCsv()" (input)="capsCsv.set($any($event.target).value)" placeholder="read, write, exec" />
                </label>
                <label>Temperatur (0-1) <input type="number" [(ngModel)]="form().temperature" name="temperature" min="0" max="1" step="0.1" /></label>
                <label>Max Tokens <input type="number" [(ngModel)]="form().maxTokens" name="maxTokens" /></label>
                <footer class="ch-agent-form-actions">
                  <button type="button" class="ch-btn" (click)="onCancelEdit()">Abbrechen</button>
                  <button type="submit" class="ch-btn ch-btn-primary"
                    [disabled]="!canSave()">{{ editingMode() === 'create' ? 'Anlegen' : 'Speichern' }}</button>
                </footer>
              </form>
            }

            @if (runStartedFor() === a.id && activeRun(); as run) {
              <div class="ch-agent-run">
                <h5>Run: {{ run.id }}</h5>
                <p>Status: <strong>{{ run.status }}</strong>, Backend: {{ run.actualCliBackend }}, Modell: {{ run.actualModel }}</p>
                @if (run.finalAnswer) {
                  <p class="ch-agent-run-answer">{{ run.finalAnswer }}</p>
                }
                <button type="button" class="ch-btn" (click)="runStartedFor.set(null)">Schliessen</button>
              </div>
            }
          </li>
        }
      </ul>

      @if (agents().length === 0) {
        <p class="ch-muted">Noch keine Custom Agents angelegt.</p>
      }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .ch-agent { display: grid; gap: 10px; }
    .ch-agent-head { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
    .ch-agent-head h4 { margin: 0; font-size: 14px; }
    .ch-muted { color: var(--muted); font-size: 12px; margin: 0; }
    .ch-error { color: #b91c1c; font-size: 12px; margin: 0; }

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

    .ch-agent-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; }
    .ch-agent-item {
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--card-bg);
    }
    .ch-agent-selected { border-color: var(--accent); }
    .ch-agent-item-head { display: flex; gap: 8px; align-items: center; }
    .ch-agent-id { font-size: 10px; color: var(--muted); font-family: var(--mono, monospace); }
    .ch-agent-desc { margin: 4px 0; font-size: 12px; }
    .ch-agent-meta { font-size: 11px; color: var(--muted); margin: 4px 0; }
    .ch-agent-meta strong { color: var(--fg); }
    .ch-agent-tags { display: flex; flex-wrap: wrap; gap: 4px; margin: 4px 0; }
    .ch-tag, .ch-cap {
      font-size: 10px;
      padding: 1px 6px;
      border-radius: 4px;
    }
    .ch-tag { background: color-mix(in srgb, var(--accent) 18%, transparent); }
    .ch-cap { background: color-mix(in srgb, #6b7280 22%, transparent); }

    .ch-agent-form {
      margin-top: 8px;
      display: grid;
      gap: 6px;
      padding: 8px;
      background: var(--bg);
      border-radius: 4px;
    }
    .ch-agent-form label {
      display: grid;
      gap: 3px;
      font-size: 11px;
    }
    .ch-agent-form input, .ch-agent-form textarea, .ch-agent-form select {
      padding: 4px 6px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--bg);
      color: var(--fg);
      font-size: 12px;
      font-family: inherit;
    }
    .ch-agent-form-actions { display: flex; gap: 6px; justify-content: flex-end; }

    .ch-agent-run {
      margin-top: 8px;
      padding: 8px;
      background: var(--bg);
      border-radius: 4px;
      font-size: 12px;
    }
    .ch-agent-run h5 { margin: 0 0 4px; font-size: 13px; }
    .ch-agent-run-answer {
      font-family: var(--mono, monospace);
      font-size: 11px;
      padding: 6px 8px;
      background: var(--card-bg);
      border-radius: 4px;
      max-height: 200px;
      overflow: auto;
    }
  `]
})
export class CustomAgentEditorComponent implements OnChanges {
  @Input() autoReload = true;
  @Output() agentRunStarted = new EventEmitter<ChAgentRunReadModel>();

  readonly svc = inject(CustomAgentService);
  readonly policy = inject(PolicyService);

  readonly agents = signal<ChAgentDefinitionReadModel[]>([]);
  readonly selectedId = signal<string | null>(null);
  readonly editing = signal(false);
  readonly editingMode = signal<'create' | 'edit'>('create');
  readonly form = signal<ChAgentDefinitionInput>({
    name: '',
    description: '',
    systemPrompt: '',
  });
  readonly tagsCsv = signal('');
  readonly capsCsv = signal('');
  readonly runStartedFor = signal<string | null>(null);
  readonly activeRun = signal<ChAgentRunReadModel | null>(null);
  readonly error = signal<string | null>(null);

  readonly canSave = computed(() => {
    const f = this.form();
    return f.name.trim().length > 0 && f.systemPrompt.trim().length > 0;
  });

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['autoReload'] && this.autoReload) {
      this.load();
    }
    if (!changes['autoReload']) {
      this.load();
    }
  }

  load(): void {
    this.svc.list().subscribe({
      next: list => this.agents.set(list),
      error: err => this.error.set(err.message ?? 'Liste konnte nicht geladen werden'),
    });
  }

  onStartCreate(): void {
    this.editingMode.set('create');
    this.editing.set(true);
    this.selectedId.set(null);
    this.form.set({ name: '', description: '', systemPrompt: '' });
    this.tagsCsv.set('');
    this.capsCsv.set('');
  }

  loadForEdit(id: string): void {
    this.svc.get(id).subscribe({
      next: agent => {
        this.editingMode.set('edit');
        this.editing.set(true);
        this.form.set({
          name: agent.name,
          description: agent.description,
          systemPrompt: agent.systemPrompt,
          preferredBackend: agent.preferredBackend,
          preferredModel: agent.preferredModel,
          temperature: agent.temperature,
          maxTokens: agent.maxTokens,
        });
        this.tagsCsv.set((agent.tags ?? []).join(', '));
        this.capsCsv.set((agent.capabilities ?? []).join(', '));
      },
      error: err => this.error.set(err.message ?? 'Agent konnte nicht geladen werden'),
    });
  }

  onCancelEdit(): void {
    this.editing.set(false);
    this.selectedId.set(null);
  }

  onSave(): void {
    if (!this.canSave()) return;
    if (!this.policy.writeModeActive()) {
      this.error.set('Write-Modus erforderlich.');
      return;
    }
    const payload: ChAgentDefinitionInput = {
      ...this.form(),
      tags: this.tagsCsv().split(',').map(s => s.trim()).filter(Boolean),
      capabilities: this.capsCsv().split(',').map(s => s.trim()).filter(Boolean) as any,
    };
    this.error.set(null);
    const obs = this.editingMode() === 'create'
      ? this.svc.create(payload)
      : this.svc.update(this.selectedId()!, payload);
    obs.subscribe({
      next: agent => {
        this.editing.set(false);
        this.selectedId.set(null);
        this.load();
      },
      error: err => this.error.set(err.message ?? 'Speichern fehlgeschlagen'),
    });
  }

  onRemove(a: ChAgentDefinitionReadModel): void {
    if (!confirm(`Agent "${a.name}" loeschen?`)) return;
    this.svc.remove(a.id).subscribe({
      next: () => this.load(),
      error: err => this.error.set(err.message ?? 'Loeschen fehlgeschlagen'),
    });
  }

  onRunStart(a: ChAgentDefinitionReadModel): void {
    const userPrompt = window.prompt(`Run-Prompt fuer "${a.name}":`, '');
    if (!userPrompt) return;
    this.runStartedFor.set(a.id);
    this.activeRun.set(null);
    this.svc.run(a.id, userPrompt).subscribe({
      next: run => {
        this.activeRun.set(run);
        this.agentRunStarted.emit(run);
      },
      error: err => {
        this.error.set(err.message ?? 'Run fehlgeschlagen');
        this.runStartedFor.set(null);
      },
    });
  }
}