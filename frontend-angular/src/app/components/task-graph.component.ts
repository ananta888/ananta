import { Component, AfterViewInit, ElementRef, ViewChild, OnDestroy, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HubApiService } from '../services/hub-api.service';
import { AgentDirectoryService } from '../services/agent-directory.service';
import mermaid from 'mermaid';

@Component({
  standalone: true,
  selector: 'app-task-graph',
  imports: [CommonModule],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Task Abhängigkeits-Graph</h2>
      <button (click)="loadTasks()" class="button-outline">🔄 Aktualisieren</button>
    </div>
    <div class="card" style="min-height: 500px; overflow: auto; display: flex; justify-content: center; align-items: center;">
      <div #mermaidDiv class="mermaid-container">
        <p *ngIf="loading" class="muted">Lade Tasks...</p>
        <p *ngIf="!loading && tasks.length === 0" class="muted">Keine Tasks zum Anzeigen gefunden.</p>
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
  @ViewChild('mermaidDiv') mermaidDiv!: ElementRef;
  tasks: any[] = [];
  loading = false;
  hub = this.dir.list().find(a => a.role === 'hub');

  constructor(
    private hubApi: HubApiService,
    private dir: AgentDirectoryService
  ) {
    mermaid.initialize({
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
      const status = t.status || 'todo';
      let color = '#fff';
      if (status === 'done') color = '#d4edda';
      else if (status === 'in-progress') color = '#fff3cd';
      else if (status === 'todo' || status === 'to-do') color = '#f8f9fa';

      // Mermaid Syntax für Nodes mit Styling (Styling über CSS Klassen oder Styles)
      // Wir nutzen einfache Labels
      const label = `${t.id}["${t.title} (${status})"]`;
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
      const { generateId } = mermaid;
      const id = 'mermaid-' + Math.random().toString(36).substr(2, 9);
      const { svg } = await mermaid.render(id, graphDefinition);
      this.mermaidDiv.nativeElement.innerHTML = svg;
    } catch (e) {
      console.error('Mermaid render error:', e);
      this.mermaidDiv.nativeElement.innerHTML = '<p class="danger">Fehler beim Rendern des Graphen</p>';
    }
  }
}
