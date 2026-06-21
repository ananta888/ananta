import { Injectable, signal, computed, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { CodeCompassService } from '../services/code-compass.service';
import { ContextPackageService } from '../services/context-package.service';
import { ChFileReadModel, ChSymbolReadModel, ChSensitiveFileDecision, ChResolveContextResponse, ChContextPackageReadModel, ChServiceError } from '../models/codehug.models';

/**
 * ContextBuilderState — UI-State fuer den Kontext-Builder (CH-003-001).
 *
 * Verwaltet:
 * - aktuelle Datei-Liste (aus CodeCompass geladen)
 * - Symbol-Liste pro Datei
 * - Auswahl (filePaths, symbolIds)
 * - Sensitive-File-Entscheidungen
 * - Aufgabenbeschreibung fuer Resolve-Context
 * - Gespeicherte Kontextpakete
 *
 * SOLID: SRP — UI-State + Cross-Service-Koordination. Persistierung ueber
 * ContextPackageService, Suchvorschlaege ueber CodeCompassService.
 */
@Injectable({ providedIn: 'root' })
export class ContextBuilderState {
  private readonly cc = inject(CodeCompassService);
  private readonly packages = inject(ContextPackageService);

  readonly currentProjectId = signal<string | null>(null);
  readonly taskDescription = signal('');
  readonly files = signal<ChFileReadModel[]>([]);
  readonly symbolsByFile = signal<Map<string, ChSymbolReadModel[]>>(new Map());
  readonly selectedFilePaths = signal<string[]>([]);
  readonly selectedSymbolIds = signal<string[]>([]);
  readonly sensitiveDecisions = signal<Record<string, ChSensitiveFileDecision>>({});
  readonly suggestions = signal<ChResolveContextResponse | null>(null);
  readonly loadingSuggestions = signal(false);
  readonly savedPackages = signal<ChContextPackageReadModel[]>([]);
  readonly packageName = signal('');
  readonly loadingFiles = signal(false);
  readonly error = signal<string | null>(null);

  readonly estimatedTokenCount = computed(() => {
    let total = 0;
    for (const path of this.selectedFilePaths()) {
      const f = this.files().find(x => x.path === path);
      if (f) total += Math.ceil(f.sizeBytes / 4);
    }
    return total;
  });

  readonly hasSelection = computed(() =>
    this.selectedFilePaths().length > 0 || this.selectedSymbolIds().length > 0,
  );

  /** Setzt das aktuelle Projekt und laedt Dateien. */
  setProject(projectId: string): void {
    this.currentProjectId.set(projectId);
    this.resetSelection();
    this.loadFiles();
    this.loadSavedPackages();
  }

  /** Laedt Dateien fuer das aktuelle Projekt. */
  loadFiles(): void {
    const id = this.currentProjectId();
    if (!id) return;
    this.loadingFiles.set(true);
    this.error.set(null);
    this.cc.listFiles(id).subscribe({
      next: files => {
        this.files.set(files);
        this.loadingFiles.set(false);
        const decisions: Record<string, ChSensitiveFileDecision> = {};
        const sensitives = this.packages.classifySensitiveFiles(files.map(f => f.path));
        for (const d of sensitives) {
          decisions[d.filePath] = d;
        }
        this.sensitiveDecisions.set(decisions);
      },
      error: err => {
        this.error.set(err.message ?? 'Dateien konnten nicht geladen werden');
        this.loadingFiles.set(false);
      },
    });
  }

  /** Laedt gespeicherte Kontextpakete fuer das aktuelle Projekt. */
  loadSavedPackages(): void {
    const id = this.currentProjectId();
    if (!id) return;
    this.packages.listForProject(id).subscribe({
      next: list => this.savedPackages.set(list),
      error: () => this.savedPackages.set([]),
    });
  }

  /** Loescht die aktuelle Auswahl. */
  resetSelection(): void {
    this.selectedFilePaths.set([]);
    this.selectedSymbolIds.set([]);
    this.suggestions.set(null);
  }

  /** Setzt die Aufgabenbeschreibung. */
  setTaskDescription(text: string): void {
    this.taskDescription.set(text);
  }

  /** Setzt den Paket-Namen. */
  setPackageName(name: string): void {
    this.packageName.set(name);
  }

  /** Fuegt eine Datei zur Auswahl hinzu (UI prueft ggf. sensitive). */
  toggleFile(path: string, included: boolean): void {
    const cur = new Set(this.selectedFilePaths());
    if (included) cur.add(path);
    else cur.delete(path);
    this.selectedFilePaths.set([...cur]);
  }

  /** Verwirft eine sensitive Datei aus der Auswahl. */
  rejectSensitiveFile(path: string): void {
    if (this.selectedFilePaths().includes(path)) {
      this.toggleFile(path, false);
    }
  }

  /** Fuegt ein Symbol zur Auswahl hinzu. */
  toggleSymbol(symbolId: string, included: boolean): void {
    const cur = new Set(this.selectedSymbolIds());
    if (included) cur.add(symbolId);
    else cur.delete(symbolId);
    this.selectedSymbolIds.set([...cur]);
  }

  /** Laedt Symbole fuer eine Datei (lazy). */
  loadSymbolsForFile(filePath: string): void {
    if (this.symbolsByFile().has(filePath)) return;
    const id = this.currentProjectId();
    if (!id) return;
    this.cc.getFileContext({ projectId: id, filePath, includeSymbols: true }).subscribe({
      next: ctx => {
        const map = new Map(this.symbolsByFile());
        map.set(filePath, ctx.symbols);
        this.symbolsByFile.set(map);
      },
      error: () => undefined,
    });
  }

  /** Loest Kontextvorschlaege auf Basis der aktuellen Aufgabe auf. */
  resolveContext(): void {
    const id = this.currentProjectId();
    const task = this.taskDescription();
    if (!id || !task.trim()) return;
    this.loadingSuggestions.set(true);
    this.error.set(null);
    this.cc.resolveContext({ projectId: id, taskDescription: task }).subscribe({
      next: resp => {
        this.suggestions.set(resp);
        this.loadingSuggestions.set(false);
      },
      error: err => {
        this.error.set(err.message ?? 'Kontext konnte nicht aufgeloest werden');
        this.loadingSuggestions.set(false);
      },
    });
  }

  /** Uebernimmt einen Vorschlag in die Auswahl. */
  acceptSuggestion(symbolId?: string, filePath?: string): void {
    if (symbolId && !this.selectedSymbolIds().includes(symbolId)) {
      this.toggleSymbol(symbolId, true);
    }
    if (filePath && !this.selectedFilePaths().includes(filePath)) {
      this.toggleFile(filePath, true);
    }
  }

  /** Laedt ein gespeichertes Paket in den Builder. */
  loadPackage(pkg: ChContextPackageReadModel): void {
    this.packageName.set(pkg.name);
    this.selectedFilePaths.set([...pkg.filePaths]);
    this.selectedSymbolIds.set([...pkg.symbolIds]);
    if (pkg.taskDescription) this.taskDescription.set(pkg.taskDescription);
  }

  /** Speichert das aktuelle Paket. Wirft ChServiceError bei Validierungsfehler. */
  saveCurrent(): Observable<ChContextPackageReadModel> {
    const id = this.currentProjectId();
    const name = this.packageName();
    if (!id) throw new ChServiceError('validation_error', 'Kein Projekt ausgewaehlt.');
    if (!name.trim()) throw new ChServiceError('validation_error', 'Paket-Name ist erforderlich.');
    if (!this.hasSelection()) throw new ChServiceError('validation_error', 'Keine Dateien oder Symbole ausgewaehlt.');

    const reasons: Record<string, string> = {};
    for (const p of this.selectedFilePaths()) reasons[p] = 'manually selected';
    for (const s of this.selectedSymbolIds()) reasons[s] = 'manually selected';
    if (this.taskDescription()) reasons['__task__'] = this.taskDescription();

    return this.packages.create({
      projectId: id,
      name,
      filePaths: this.selectedFilePaths(),
      symbolIds: this.selectedSymbolIds(),
      reasons,
      taskDescription: this.taskDescription(),
    });
  }
}