import { Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SourcesService } from '../../services/sources.service';

@Component({
  standalone: true,
  selector: 'app-source-import-dialog',
  imports: [FormsModule],
  templateUrl: './source-import-dialog.component.html',
})
export class SourceImportDialogComponent {
  @Input() open = false;
  @Output() closed = new EventEmitter<boolean>();
  mode: 'export' | 'text' = 'export';
  jsonText = '';
  title = '';
  textContent = '';
  collectionName = '';
  error = '';
  result: any = null;
  submitting = false;

  constructor(private readonly sources: SourcesService) {}

  readFile(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    file.text().then(text => { this.jsonText = text; }).catch(() => { this.error = 'file_read_failed'; });
  }

  submit(): void {
    this.error = '';
    let payload: Record<string, any>;
    try {
      if (this.mode === 'export') {
        payload = JSON.parse(this.jsonText);
      } else {
        if (!this.title.trim() || !this.textContent.trim()) throw new Error('text_source_required');
        payload = this.sources.directTextExport(this.title, this.textContent, this.collectionName);
      }
    } catch (error: any) {
      this.error = String(error?.message || 'invalid_json');
      return;
    }
    this.submitting = true;
    this.sources.importOpenNotebook(payload, this.mode === 'export' ? this.collectionName : undefined).subscribe({
      next: result => { this.result = result; this.submitting = false; },
      error: error => {
        this.error = String(error?.error?.data?.reason_code || error?.error?.error || error?.message || 'import_failed');
        this.submitting = false;
      },
    });
  }

  close(imported = false): void {
    this.closed.emit(imported || Boolean(this.result));
  }
}
