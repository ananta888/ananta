import { Injectable, signal, computed, inject } from '@angular/core';
import { CodeCompassService } from '../services/code-compass.service';
import { AgentRunService } from '../services/agent-run.service';
import { PolicyService } from '../services/policy.service';
import { ContextPackageService } from '../services/context-package.service';
import { ChProjectReadModel, ChAgentRunReadModel } from '../models/codehug.models';

/**
 * CodeHugFacade — zentraler UI-State fuer das CodeHug-Feature.
 *
 * SOLID: SRP — UI-State + Cross-Service-Koordination. KEINE HTTP-Calls
 * direkt; alle Daten kommen ueber die spezialisierten Services.
 *
 * Signals:
 * - currentProjectId(): aktuell ausgewaehltes Projekt (oder null)
 * - currentProject(): Metadaten des aktuellen Projekts
 * - projects(): bekannte Projekte
 * - recentRuns(): letzte Agent-Runs (cross-project)
 *
 * State-Management:
 * - loadProject(id) ladet Metadaten + Sensitive-Patterns aus Policy
 * - clearProject() setzt alles zurueck
 */
@Injectable({ providedIn: 'root' })
export class CodeHugFacade {
  private readonly cc = inject(CodeCompassService);
  private readonly runs = inject(AgentRunService);
  private readonly policy = inject(PolicyService);
  private readonly packages = inject(ContextPackageService);

  readonly currentProjectId = signal<string | null>(null);
  readonly projects = signal<ChProjectReadModel[]>([]);
  readonly recentRuns = signal<ChAgentRunReadModel[]>([]);
  readonly loadingProject = signal(false);
  readonly projectError = signal<string | null>(null);

  readonly currentProject = computed(() => {
    const id = this.currentProjectId();
    if (!id) return null;
    return this.projects().find(p => p.id === id) ?? null;
  });

  readonly hasProject = computed(() => this.currentProjectId() !== null);

  /**
   * Lädt alle bekannten Projekte. Wird beim Dashboard-Start aufgerufen.
   */
  loadProjects(): void {
    this.cc.listProjects().subscribe({
      next: list => this.projects.set(list),
      error: err => this.projectError.set(err.message ?? 'Projekte konnten nicht geladen werden'),
    });
  }

  /**
   * Wählt ein Projekt aus und lädt Metadaten + Sensitive-Patterns.
   */
  selectProject(projectId: string): void {
    this.currentProjectId.set(projectId);
    this.loadingProject.set(true);
    this.projectError.set(null);

    this.cc.getProject(projectId).subscribe({
      next: project => {
        // Replace or insert
        const list = [...this.projects()];
        const idx = list.findIndex(p => p.id === projectId);
        if (idx >= 0) list[idx] = project;
        else list.push(project);
        this.projects.set(list);
        this.loadingProject.set(false);
      },
      error: err => {
        this.projectError.set(err.message ?? 'Projekt konnte nicht geladen werden');
        this.loadingProject.set(false);
      },
    });

    // Sensitive-Patterns aus Policy laden
    this.policy.loadCurrentSnapshot().subscribe({
      next: snap => {
        if (snap.sensitiveFilePatterns.length > 0) {
          this.packages.setSensitivePatterns(snap.sensitiveFilePatterns);
        }
      },
      // Kein Hard-Fail: bei Policy-Load-Fehler bleiben Default-Patterns aktiv.
      error: () => undefined,
    });

    // Letzte Runs für dieses Projekt nachladen
    this.runs.listRuns(projectId).subscribe({
      next: list => this.recentRuns.set(list.slice(0, 10)),
      error: () => this.recentRuns.set([]),
    });
  }

  /**
   * Aktualisiert die Metadaten des aktuellen Projekts (z.B. nach Re-Index).
   */
  refreshCurrentProject(): void {
    const id = this.currentProjectId();
    if (!id) return;
    this.selectProject(id);
  }

  /**
   * Löscht die aktuelle Auswahl.
   */
  clearProject(): void {
    this.currentProjectId.set(null);
    this.projectError.set(null);
    this.recentRuns.set([]);
  }

  /**
   * Stösst eine Re-Indexierung des aktuellen Projekts an.
   */
  triggerReindex(): void {
    const id = this.currentProjectId();
    if (!id) return;
    this.cc.triggerReindex(id).subscribe({
      next: () => this.refreshCurrentProject(),
      error: err => this.projectError.set(err.message ?? 'Re-Indexierung fehlgeschlagen'),
    });
  }
}