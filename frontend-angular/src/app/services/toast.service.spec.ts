import { describe, it, expect, beforeEach } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import { ToastService, ToastMessage } from './toast.service';

describe('ToastService', () => {
  let service: ToastService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [ToastService]
    });
    service = TestBed.inject(ToastService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should emit success toast message', async () => {
    const testMessage = 'Operation successful';
    const testDuration = 3000;
    const toastPromise = firstValueFrom(service.toasts$);
    service.success(testMessage, testDuration);
    const toast: ToastMessage = await toastPromise;
    expect(toast.type).toBe('success');
    expect(toast.message).toBe(testMessage);
    expect(toast.duration).toBe(testDuration);
  });

  it('should emit error toast message with default duration', async () => {
    const testMessage = 'An error occurred';
    const toastPromise = firstValueFrom(service.toasts$);
    service.error(testMessage);
    const toast: ToastMessage = await toastPromise;
    expect(toast.type).toBe('error');
    expect(toast.message).toBe(testMessage);
    expect(toast.duration).toBe(5000);
  });

  it('should emit info toast message', async () => {
    const testMessage = 'Information message';
    const testDuration = 2000;
    const toastPromise = firstValueFrom(service.toasts$);
    service.info(testMessage, testDuration);
    const toast: ToastMessage = await toastPromise;
    expect(toast.type).toBe('info');
    expect(toast.message).toBe(testMessage);
    expect(toast.duration).toBe(testDuration);
  });

  it('should emit warning toast message with default duration', async () => {
    const testMessage = 'Warning message';
    const toastPromise = firstValueFrom(service.toasts$);
    service.warning(testMessage);
    const toast: ToastMessage = await toastPromise;
    expect(toast.type).toBe('warning');
    expect(toast.message).toBe(testMessage);
    expect(toast.duration).toBe(4000);
  });

  it('should emit multiple toast messages in sequence', () => {
    const messages: ToastMessage[] = [];

    service.toasts$.subscribe((toast: ToastMessage) => {
      messages.push(toast);
    });

    service.success('First message');
    service.error('Second message');
    service.info('Third message');

    expect(messages.length).toBe(3);
    expect(messages[0].type).toBe('success');
    expect(messages[1].type).toBe('error');
    expect(messages[2].type).toBe('info');
  });
});
