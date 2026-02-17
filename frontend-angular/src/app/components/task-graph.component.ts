import { Component, AfterViewInit, ElementRef, ViewChild, OnDestroy, OnInit, inject } from '@angular/core';

import { HubApiService } from '../services/hub-api.service';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { normalizeTaskStatus, taskStatusDisplayLabel } from '../utils/task-status';
import 'mermaid/dist/mermaid.min.js';

declare global {
  interface Window {
    mermaid?: {
      initialize: (config: any) => void;
      render: (id: string, definition: string) => Promise<{ svg: string }>;
    };
  }
}

@Component({
  standalone: true,
  selector: 'app-task-graph',
  imports: [],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Task Abhängigkeits-Graph</h2>
      <button (click)="loadTasks()" class="button-outline">🔄 Aktualisieren</button>
    </div>
    <div class="card" style="min-height: 500px; overflow: auto; display: flex; justify-content: center; align-items: center;">
      <div #mermaidDiv class="mermaid-container">
        @if (loading) {
          <p class="muted">Lade Tasks...</p>
        }
        @if (!loading && tasks.length === 0) {
          <p class="muted">Keine Tasks zum Anzeigen gefunden.</p>
        }
      </div>
    </div>
    <style>
      .mermaid-container {
      width: 100%;
      text-align: center;
    }
    .mermaid-container svg {
    max-width: 100%;
    height: auto;
    }
    </style>
    `
})
export class TaskGraphComponent implements OnInit, AfterViewInit {
  private hubApi = inject(HubApiService);
  private dir = inject(AgentDirectoryService);

  @ViewChild('mermaidDiv') mermaidDiv!: ElementRef;
  tasks: any[] = [];
  loading = false;
  hub = this.dir.list().find(a => a.role === 'hub');

  constructor() {
    window.mermaid?.initialize({
      startOnLoad: false,
      theme: 'default',
      securityLevel: 'loose',
    });
  }

  ngOnInit() {
    this.loadTasks();
  }

  ngAfterViewInit() {
    if (this.tasks.length > 0) {
      this.renderGraph();
    }
  }

  loadTasks() {
    if (!this.hub) return;
    this.loading = true;
    this.hubApi.listTasks(this.hub.url).subscribe({
      next: (r) => {
        this.tasks = Array.isArray(r) ? r : [];
        this.loading = false;
        setTimeout(() => this.renderGraph(), 0);
      },
      error: () => {
        this.loading = false;
      }
    });
  }

  async renderGraph() {
    if (!this.mermaidDiv || this.tasks.length === 0) return;

    let graphDefinition = 'graph TD\n';
    
    // Nodes definieren
    this.tasks.forEach(t => {
      const status = normalizeTaskStatus(t.status);
      const statusLabel = taskStatusDisplayLabel(status);
      let color = '#fff';
      if (status === 'completed') color = '#d4edda';
      else if (status === 'in_progress') color = '#fff3cd';
      else if (status === 'todo') color = '#f8f9fa';

      // Mermaid Syntax für Nodes mit Styling (Styling über CSS Klassen oder Styles)
      // Wir nutzen einfache Labels
      const label = `${t.id}["${t.title} (${statusLabel})"]`;
      graphDefinition += `  ${label}\n`;
      graphDefinition += `  style ${t.id} fill:${color},stroke:#333,stroke-width:1px\n`;
    });

    // Edges (Parent -> Subtask)
    this.tasks.forEach(t => {
      if (t.parent_task_id) {
        graphDefinition += `  ${t.parent_task_id} --> ${t.id}\n`;
      }
    });

    try {
      const mermaidApi = window.mermaid;
      if (!mermaidApi) {
        this.mermaidDiv.nativeElement.innerHTML = '<p class="danger">Mermaid nicht geladen</p>';
        return;
      }
      const id = 'mermaid-' + Math.random().toString(36).substr(2, 9);
      const { svg } = await mermaidApi.render(id, graphDefinition);
      this.mermaidDiv.nativeElement.innerHTML = svg;
    } catch (e) {
      console.error('Mermaid render error:', e);
      this.mermaidDiv.nativeElement.innerHTML = '<p class="danger">Fehler beim Rendern des Graphen</p>';
    }
  }

}
