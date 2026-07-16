import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { ModelTrainingFacade } from '../model-training.facade';
import { TrainingCapabilities } from '../model-training.models';
import { AdapterImportComponent } from './adapter-import.component';

describe('AdapterImportComponent', () => {
  const facade = {
    capabilities: signal<TrainingCapabilities | null>({
      available: true, backends: [], gpu_profiles: [], base_models: [], limits: { max_adapter_bytes: 1000 },
    }),
    importAdapter: vi.fn(() => of({
      id: 'adapter-1', name: 'Adapter', version: 1, base_model_id: 'model-1', status: 'imported_pending_evaluation',
    })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      imports: [AdapterImportComponent],
      providers: [{ provide: ModelTrainingFacade, useValue: facade }],
    });
  });

  it('accepts only a safe bundle or the exact config/safetensors pair and never claims approval', () => {
    const fixture = TestBed.createComponent(AdapterImportComponent);
    const component = fixture.componentInstance;
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Import ist keine Freigabe');

    component.name = 'Adapter';
    component.baseModelId = 'model-1';
    component.selectConfig({ target: { files: [new File(['{}'], 'wrong.json')] } } as unknown as Event);
    expect(component.canImport()).toBe(false);
    expect(component.error).toContain('adapter_config.json');

    component.selectBundle({ target: { files: [new File(['zip'], 'adapter.zip')] } } as unknown as Event);
    expect(component.canImport()).toBe(true);
    component.importAdapter();
    expect(facade.importAdapter).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Adapter', base_model_id: 'model-1' }),
      expect.stringMatching(/^adapter-import-/),
    );
  });

  it('shows actionable recovery for a rejected import', () => {
    facade.importAdapter.mockReturnValueOnce(throwError(() => ({ status: 422 })) as any);
    const component = TestBed.createComponent(AdapterImportComponent).componentInstance;
    component.name = 'Adapter';
    component.baseModelId = 'model-1';
    component.bundle = new File(['zip'], 'adapter.zip');

    component.importAdapter();

    expect(component.error).toContain('erneut validieren');
    expect(component.busy).toBe(false);
  });
});
