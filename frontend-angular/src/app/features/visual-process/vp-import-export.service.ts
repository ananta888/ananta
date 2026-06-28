import { Injectable, inject } from '@angular/core';
import { from, Observable } from 'rxjs';
import { switchMap, tap } from 'rxjs/operators';

import {
  BpmnExportResult,
  BpmnImportResult,
  VisualProcessApiService,
  VpGraph,
} from './visual-process-api.service';

@Injectable()
export class VpImportExportService {
  private api = inject(VisualProcessApiService);

  exportBpmn(graph: VpGraph): Observable<BpmnExportResult> {
    return this.api.exportBpmn(graph).pipe(
      tap(result => this.download(result.bpmn_xml, `${graph.name}.bpmn`, 'application/xml')),
    );
  }

  importBpmn(file: File): Observable<BpmnImportResult> {
    return from(file.text()).pipe(switchMap(xml => this.api.importBpmn(xml)));
  }

  mermaid(graph: VpGraph): Observable<{ mermaid: string; tui?: string }> {
    return this.api.mermaid(graph);
  }

  copyMermaid(source: string): Promise<void> {
    return navigator.clipboard?.writeText(source) ?? Promise.resolve();
  }

  downloadMermaid(source: string, graphName: string): void {
    this.download(`\`\`\`mermaid\n${source}\n\`\`\``, `${graphName}.md`, 'text/markdown');
  }

  private download(content: string, filename: string, type: string): void {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }
}
