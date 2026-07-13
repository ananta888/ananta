import { TestBed } from '@angular/core/testing';
import { OpsLogViewerComponent } from './ops-log-viewer.component';

describe('OpsLogViewerComponent', () => {
  it('keeps stdout and stderr visibly separated and bounds requested tail', async () => {
    await TestBed.configureTestingModule({ imports: [OpsLogViewerComponent] }).compileComponents();
    const fixture = TestBed.createComponent(OpsLogViewerComponent);
    fixture.componentRef.setInput('content', 'application ready');
    fixture.componentRef.setInput('stderr', 'driver warning');
    fixture.componentInstance.tail = 5000;
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('application ready');
    expect(fixture.nativeElement.textContent).toContain('stderr / Diagnoseausgabe');
    expect(fixture.componentInstance.normalizedTail()).toBe(1000);
  });
});
