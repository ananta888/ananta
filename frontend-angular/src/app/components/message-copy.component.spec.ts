import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ClipboardService } from '../services/clipboard.service';
import { NotificationService } from '../services/notification.service';
import { NotificationsComponent } from './notifications.component';
import { ToastComponent } from './toast.component';

describe('global message copy actions', () => {
  const clipboard = { copyText: vi.fn(() => Promise.resolve(true)) };

  beforeEach(() => {
    clipboard.copyText.mockClear();
  });

  it('copies a toast message', async () => {
    await TestBed.configureTestingModule({
      imports: [ToastComponent],
      providers: [{ provide: ClipboardService, useValue: clipboard }],
    }).compileComponents();
    const fixture = TestBed.createComponent(ToastComponent);
    fixture.componentInstance.activeToasts = [{
      id: 1,
      type: 'error',
      message: 'Toast details',
      duration: 0,
    }];
    fixture.detectChanges();

    clickCopyButton(fixture);

    expect(clipboard.copyText).toHaveBeenCalledWith('Toast details');
  });

  it('copies a notification message', async () => {
    await TestBed.configureTestingModule({
      imports: [NotificationsComponent],
      providers: [{ provide: ClipboardService, useValue: clipboard }],
    }).compileComponents();
    const fixture = TestBed.createComponent(NotificationsComponent);
    fixture.detectChanges();

    TestBed.inject(NotificationService).error('Notification details', 0);
    await Promise.resolve();
    fixture.detectChanges();
    clickCopyButton(fixture);

    expect(clipboard.copyText).toHaveBeenCalledWith('Notification details');
  });
});

function clickCopyButton(fixture: ComponentFixture<unknown>) {
  const button = fixture.nativeElement.querySelector(
    'button[aria-label="Meldung kopieren"]',
  ) as HTMLButtonElement | null;
  expect(button).not.toBeNull();
  button?.click();
}
