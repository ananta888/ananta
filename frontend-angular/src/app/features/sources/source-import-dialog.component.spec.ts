import { of } from 'rxjs';
import { SourceImportDialogComponent } from './source-import-dialog.component';

describe('SourceImportDialogComponent', () => {
  function service(): any {
    return {
      importOpenNotebook: vi.fn(() => of({ status: 'completed', imported: { sources: 1 } })),
      directTextExport: vi.fn(() => ({ schema: 'open_notebook_export.v1' })),
    };
  }

  it('imports pasted export JSON', () => {
    const api = service();
    const component = new SourceImportDialogComponent(api);
    component.jsonText = '{"schema":"open_notebook_export.v1"}';
    component.submit();
    expect(api.importOpenNotebook).toHaveBeenCalled();
    expect(component.result.status).toBe('completed');
  });

  it('imports direct text and rejects empty input', () => {
    const api = service();
    const component = new SourceImportDialogComponent(api);
    component.mode = 'text';
    component.submit();
    expect(component.error).toBe('text_source_required');
    component.title = 'Title';
    component.textContent = 'Grounded content';
    component.submit();
    expect(api.directTextExport).toHaveBeenCalled();
  });

  it('reports invalid JSON inline', () => {
    const component = new SourceImportDialogComponent(service());
    component.jsonText = '{';
    component.submit();
    expect(component.error).toBeTruthy();
  });
});
