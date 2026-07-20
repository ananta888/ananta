import { TestBed } from '@angular/core/testing';

import { SemanticReceiverPathPanelComponent } from './semantic-receiver-path-panel.component';

describe('SemanticReceiverPathPanelComponent', () => {
  it('shows desired and effective paths separately and emits a receiver-scoped intent', async () => {
    await TestBed.configureTestingModule({ imports: [SemanticReceiverPathPanelComponent] }).compileComponents();
    const fixture = TestBed.createComponent(SemanticReceiverPathPanelComponent);
    fixture.componentInstance.receivers = [{
      receiverId: 'bob', label: 'Bob', preference: 'sfu', effectivePath: 'ordinary',
      pendingHubConfirmation: true, reasonCode: 'receiver_path_hub_confirmation_required',
    }];
    const intents: unknown[] = [];
    fixture.componentInstance.intent.subscribe(value => intents.push(value));
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Effektiv: Ordinary Media');
    expect(fixture.nativeElement.textContent).toContain('Hub-Bestätigung ausstehend');
    const select = fixture.nativeElement.querySelector('select') as HTMLSelectElement;
    select.value = 'ordinary'; select.dispatchEvent(new Event('change')); fixture.detectChanges();
    expect(intents).toEqual([{ receiverId: 'bob', preference: 'ordinary' }]);
  });
});
