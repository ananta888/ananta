import { Component, ChangeDetectionStrategy, OnInit, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import { CodeHugFacade } from '../state/codehug.facade';
import { ContextBuilderState } from '../state/context-builder.state';
import { ChFileReadModel, ChSymbolReadModel } from '../models/codehug.models';

/**
 * ContextBuilderComponent — Kontextpaket-Builder (CH-003-001).
 *
 * Drei-Spalter-Layout:
 * - Links: Datei-Baum (gruppiert nach Verzeichnis), Sensitive-Files markiert
 * - Mitte: Aufgabe eingeben + Resolve-Context Suggestions
 * - Rechts: aktuelle Auswahl + Save-Button + gespeicherte Pakete
 */
@Component({
  selector: 'ch-context-builder',
  standalone: true,
  imports: [DatePipe, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="ch-cb">
      <!-- Aufgabe -->
      <div class="ch-cb-row ch-cb-task">
        <label class="ch-cb-label" for="ch-cb-task-input">Aufgabe (optional, fuer Resolve-Context)</label>
        <div class="ch-cb-task-row">
          <input
            id="ch-cb-task-input"
            type="text"
            class="ch-input"
            [ngModel]="state.taskDescription()"
            (ngModelChange)="state.setTaskDescription($event)"
            placeholder="z.B. 'finde alle Stellen die User.email setzen'" />
          <button
            type="button"
            class="ch-btn ch-btn-primary"
            [disabled]="!state.taskDescription().trim() || state.loadingSuggestions()"
            (click)="state.resolveContext()">
            {{ state.loadingSuggestions() ? 'Aufloesen…' : 'Vorschlaege' }}
          </button>
        </div>
      </div>

      @if (state.suggestions(); as sugg) {
        <div class="ch-cb-row">
          <h4>Vorschlaege</h4>
          @if (sugg.suggestions.length === 0) {
            <p class="ch-muted">Keine Vorschlaege gefunden.</p>
          } @else {
            <ul class="ch-cb-suggestions">
              @for (s of sugg.suggestions; track $index) {
                <li class="ch-cb-suggestion" [attr.data-source]="s.source">
                  <span class="ch-cb-suggestion-source">{{ s.source }}</span>
                  <span class="ch-cb-suggestion-target ch-mono">{{ s.symbolId ?? s.filePath }}</span>
                  <span class="ch-cb-suggestion-score">{{ (s.relevanceScore * 100).toFixed(0) }}%</span>
                  <span class="ch-cb-suggestion-reason">{{ s.reason }}</span>
                  <button
                    type="button"
                    class="ch-btn ch-btn-mini"
                    (click)="state.acceptSuggestion(s.symbolId, s.filePath)">
                    Uebernehmen
                  </button>
                </li>
              }
            </ul>
          }
        </div>
      }

      <!-- Drei-Spalter -->
      <div class="ch-cb-grid">
        <!-- Links: Datei-Baum -->
        <section class="ch-cb-col" aria-labelledby="ch-cb-files-h">
          <h4 id="ch-cb-files-h">Dateien</h4>
          @if (state.loadingFiles()) {
            <p class="ch-muted">Lade Dateien…</p>
          } @else if (state.error(); as err) {
            <p class="ch-error" role="alert">{{ err }}</p>
          } @else if (!state.currentProjectId()) {
            <p class="ch-muted">Projekt auswaehlen (im Dashboard) um Dateien zu sehen.</p>
          } @else if (state.files().length === 0) {
            <p class="ch-muted">Keine Dateien im Projekt.</p>
          } @else {
            @for (group of fileGroups(); track group.dir) {
              <details open class="ch-cb-dir">
                <summary>{{ group.dir || '(root)' }} <span class="ch-cb-count">{{ group.files.length }}</span></summary>
                <ul class="ch-cb-file-list">
                  @for (f of group.files; track f.path) {
                    <li
                      class="ch-cb-file"
                      [class.ch-cb-file-selected]="isFileSelected(f.path)"
                      [class.ch-cb-file-sensitive]="isSensitive(f.path)">
                      <label class="ch-cb-file-label">
                        <input
                          type="checkbox"
                          [checked]="isFileSelected(f.path)"
                          [disabled]="isSensitive(f.path)"
                          (change)="onFileToggle(f, $any($event.target).checked)" />
                        <span class="ch-cb-file-name ch-mono">{{ basename(f.path) }}</span>
                        @if (isSensitive(f.path)) {
                          <span class="ch-cb-sensitive-badge" [title]="sensitivePattern(f.path)">sensitive</span>
                        }
                        <span class="ch-cb-file-lang">{{ f.language }}</span>
                      </label>
                      @if (isFileSelected(f.path)) {
                        <button
                          type="button"
                          class="ch-btn ch-btn-mini"
                          (click)="state.loadSymbolsForFile(f.path)">
                          {{ symbolsLoaded(f.path) ? 'Symbole laden' : 'Symbole anzeigen' }}
                        </button>
                      }
                      @if (symbolsLoaded(f.path)) {
                        <ul class="ch-cb-symbols">
                          @for (s of symbolsFor(f.path); track s.id) {
                            <li>
                              <label class="ch-cb-symbol-label">
                                <input
                                  type="checkbox"
                                  [checked]="isSymbolSelected(s.id)"
                                  (change)="state.toggleSymbol(s.id, $any($event.target).checked)" />
                                <span class="ch-cb-symbol-kind">{{ s.kind }}</span>
                                <span class="ch-cb-symbol-name ch-mono">{{ s.name }}</span>
                              </label>
                            </li>
                          }
                        </ul>
                      }
                    </li>
                  }
                </ul>
              </details>
            }
          }
        </section>

        <!-- Mitte: Aufgaben-Details + gespeicherte Pakete -->
        <section class="ch-cb-col" aria-labelledby="ch-cb-mid-h">
          <h4 id="ch-cb-mid-h">Paket</h4>
          <label class="ch-cb-label" for="ch-cb-name">Name</label>
          <input
            id="ch-cb-name"
            type="text"
            class="ch-input"
            [ngModel]="state.packageName()"
            (ngModelChange)="state.setPackageName($event)"
            placeholder="z.B. 'auth-refactor-context'" />

          <p class="ch-cb-meta">
            Dateien: <strong>{{ state.selectedFilePaths().length }}</strong> |
            Symbole: <strong>{{ state.selectedSymbolIds().length }}</strong> |
            Geschaetzt: <strong>{{ state.estimatedTokenCount() }} tokens</strong>
          </p>

          <div class="ch-cb-actions">
            <button
              type="button"
              class="ch-btn ch-btn-secondary"
              (click)="state.resetSelection()"
              [disabled]="!state.hasSelection()">
              Auswahl loeschen
            </button>
            <button
              type="button"
              class="ch-btn ch-btn-primary"
              (click)="onSave()"
              [disabled]="!state.hasSelection() || !state.packageName().trim() || saving()">
              {{ saving() ? 'Speichern…' : 'Speichern' }}
            </button>
          </div>
          @if (saveError(); as err) {
            <p class="ch-error" role="alert">{{ err }}</p>
          }
          @if (saveSuccess()) {
            <p class="ch-success">Paket gespeichert.</p>
          }
        </section>

        <!-- Rechts: gespeicherte Pakete -->
        <section class="ch-cb-col" aria-labelledby="ch-cb-saved-h">
          <h4 id="ch-cb-saved-h">Gespeicherte Pakete</h4>
          @if (state.savedPackages().length === 0) {
            <p class="ch-muted">Keine gespeicherten Pakete.</p>
          } @else {
            <ul class="ch-cb-saved">
              @for (pkg of state.savedPackages(); track pkg.id) {
                <li class="ch-cb-saved-item">
                  <button
                    type="button"
                    class="ch-cb-saved-btn"
                    (click)="state.loadPackage(pkg)"
                    [title]="'v' + pkg.version + ' · ' + (pkg.updatedAt | date: 'short')">
                    <strong>{{ pkg.name }}</strong>
                    <span class="ch-cb-saved-meta">{{ pkg.filePaths.length }} Dateien, v{{ pkg.version }}</span>
                  </button>
                </li>
              }
            </ul>
          }
        </section>
      </div>
    </section>
  `,
  styles: [`
    :host { display: block; padding: 14px; }
    .ch-cb { display: grid; gap: 12px; }
    .ch-cb-row { display: grid; gap: 6px; }
    .ch-cb-label { font-size: 11px; letter-spacing: 0.6px; text-transform: uppercase; color: var(--muted); }
    .ch-cb-task-row { display: flex; gap: 8px; }
    .ch-input {
      flex: 1;
      padding: 6px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--fg);
      font-size: 13px;
    }
    .ch-btn {
      padding: 5px 10px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 12px;
    }
    .ch-btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    .ch-btn-secondary { background: var(--card-bg); }
    .ch-btn-mini { padding: 2px 6px; font-size: 11px; }
    .ch-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .ch-muted { color: var(--muted); font-size: 12px; margin: 4px 0; }
    .ch-error { color: #b91c1c; font-size: 12px; margin: 4px 0; }
    .ch-success { color: #065f46; font-size: 12px; margin: 4px 0; }

    .ch-cb-grid {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 12px;
    }
    @media (max-width: 900px) {
      .ch-cb-grid { grid-template-columns: 1fr; }
    }
    .ch-cb-col {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
      background: var(--card-bg);
      max-height: 65vh;
      overflow: auto;
    }
    .ch-cb-col h4 { margin: 0 0 8px; font-size: 13px; }

    .ch-cb-dir {
      margin-bottom: 4px;
    }
    .ch-cb-dir summary {
      cursor: pointer;
      font-size: 12px;
      font-weight: 600;
      padding: 4px 0;
    }
    .ch-cb-count {
      margin-left: 4px;
      color: var(--muted);
      font-weight: 400;
    }
    .ch-cb-file-list, .ch-cb-symbols {
      list-style: none;
      padding: 0 0 0 8px;
      margin: 0;
    }
    .ch-cb-file {
      padding: 2px 0;
      border-bottom: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
    }
    .ch-cb-file:last-child { border-bottom: none; }
    .ch-cb-file-label {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      cursor: pointer;
    }
    .ch-cb-file-name { flex: 1; }
    .ch-cb-file-lang {
      font-size: 10px;
      color: var(--muted);
      padding: 1px 5px;
      border-radius: 3px;
      background: var(--bg);
    }
    .ch-cb-file-sensitive .ch-cb-file-name { opacity: 0.7; }
    .ch-cb-sensitive-badge {
      font-size: 9px;
      padding: 1px 5px;
      border-radius: 3px;
      background: color-mix(in srgb, #f59e0b 30%, transparent);
      color: #92400e;
      font-weight: 700;
    }
    .ch-cb-symbol-label {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      padding: 1px 0;
      cursor: pointer;
    }
    .ch-cb-symbol-kind {
      color: var(--muted);
      font-size: 10px;
      width: 60px;
    }

    .ch-cb-meta {
      margin: 8px 0;
      font-size: 12px;
      color: var(--muted);
    }
    .ch-cb-meta strong { color: var(--fg); }
    .ch-cb-actions { display: flex; gap: 6px; margin-bottom: 6px; }

    .ch-cb-suggestions {
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 4px;
    }
    .ch-cb-suggestion {
      display: grid;
      grid-template-columns: max-content 1fr max-content 2fr max-content;
      gap: 8px;
      align-items: center;
      padding: 4px 6px;
      border: 1px solid var(--border);
      border-radius: 4px;
      font-size: 11px;
    }
    .ch-cb-suggestion-source {
      padding: 1px 6px;
      border-radius: 3px;
      background: color-mix(in srgb, var(--accent) 18%, transparent);
      font-weight: 600;
      font-size: 10px;
    }
    .ch-cb-suggestion-score { font-weight: 600; }
    .ch-cb-suggestion-reason { color: var(--muted); }

    .ch-cb-saved { list-style: none; padding: 0; margin: 0; display: grid; gap: 4px; }
    .ch-cb-saved-btn {
      width: 100%;
      text-align: left;
      padding: 6px 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      display: grid;
      gap: 2px;
    }
    .ch-cb-saved-meta { font-size: 10px; color: var(--muted); }
    .ch-mono { font-family: var(--mono, ui-monospace, monospace); font-size: 11px; }
  `]
})
export class CodeHugContextBuilderComponent implements OnInit {
  readonly facade = inject(CodeHugFacade);
  readonly state = inject(ContextBuilderState);

  readonly saving = signal(false);
  readonly saveError = signal<string | null>(null);
  readonly saveSuccess = signal(false);

  ngOnInit(): void {
    // Wenn das facade ein Projekt hat, uebernehme es in den builder-state
    const id = this.facade.currentProjectId();
    if (id) {
      this.state.setProject(id);
    } else {
      this.facade.loadProjects();
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // File helpers
  // ─────────────────────────────────────────────────────────────────────────

  fileGroups(): { dir: string; files: ChFileReadModel[] }[] {
    const groups = new Map<string, ChFileReadModel[]>();
    for (const f of this.state.files()) {
      const dir = f.path.includes('/') ? f.path.substring(0, f.path.lastIndexOf('/')) : '';
      const arr = groups.get(dir) ?? [];
      arr.push(f);
      groups.set(dir, arr);
    }
    return [...groups.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([dir, files]) => ({ dir, files: files.sort((a, b) => a.path.localeCompare(b.path)) }));
  }

  basename(path: string): string {
    const i = path.lastIndexOf('/');
    return i >= 0 ? path.substring(i + 1) : path;
  }

  isFileSelected(path: string): boolean {
    return this.state.selectedFilePaths().includes(path);
  }

  isSensitive(path: string): boolean {
    return this.state.sensitiveDecisions()[path]?.decision === 'requires-confirmation';
  }

  sensitivePattern(path: string): string {
    return this.state.sensitiveDecisions()[path]?.matchedPattern ?? '';
  }

  onFileToggle(file: ChFileReadModel, checked: boolean): void {
    if (!checked && this.isFileSelected(file.path)) {
      this.state.toggleFile(file.path, false);
      return;
    }
    if (checked && this.isSensitive(file.path)) {
      // Sensitive: explizite Bestaetigung verlangen — hier: alert-toast-ersatz
      const ok = confirm(`Datei "${file.path}" ist als sensitiv markiert (Pattern: ${this.sensitivePattern(file.path)}). Wirklich aufnehmen?`);
      if (!ok) return;
    }
    this.state.toggleFile(file.path, checked);
    if (checked) {
      this.state.loadSymbolsForFile(file.path);
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Symbol helpers
  // ─────────────────────────────────────────────────────────────────────────

  symbolsLoaded(path: string): boolean {
    return this.state.symbolsByFile().has(path);
  }

  symbolsFor(path: string): ChSymbolReadModel[] {
    return this.state.symbolsByFile().get(path) ?? [];
  }

  isSymbolSelected(id: string): boolean {
    return this.state.selectedSymbolIds().includes(id);
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Save
  // ─────────────────────────────────────────────────────────────────────────

  async onSave(): Promise<void> {
    this.saving.set(true);
    this.saveError.set(null);
    this.saveSuccess.set(false);
    try {
      await firstValueFrom(this.state.saveCurrent());
      this.saveSuccess.set(true);
      this.state.loadSavedPackages();
      this.state.resetSelection();
    } catch (err: any) {
      this.saveError.set(err?.message ?? 'Speichern fehlgeschlagen');
    } finally {
      this.saving.set(false);
    }
  }
}