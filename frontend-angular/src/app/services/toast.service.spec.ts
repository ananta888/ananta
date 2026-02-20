import { describe, it, expect, beforeEach } from 'vitest';
import { TestBed } from '@angular/core/testing';
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

  it('should emit success toast message', (done) => {
    const testMessage = 'Operation successful';
    const testDuration = 3000;

    service.toasts$.subscribe((toast: ToastMessage) => {
      expect(toast.type).toBe('success');
      expect(toast.message).toBe(testMessage);
      expect(toast.duration).toBe(testDuration);
      done();
    });

    service.success(testMessage, testDuration);
  });

  it('should emit error toast message with default duration', (done) => {
    const testMessage = 'An error occurred';

    service.toasts$.subscribe((toast: ToastMessage) => {
      expect(toast.type).toBe('error');
      expect(toast.message).toBe(testMessage);
      expect(toast.duration).toBe(5000);
      done();
    });

    service.error(testMessage);
  });

  it('should emit info toast message', (done) => {
    const testMessage = 'Information message';
    const testDuration = 2000;

    service.toasts$.subscribe((toast: ToastMessage) => {
      expect(toast.type).toBe('info');
      expect(toast.message).toBe(testMessage);
      expect(toast.duration).toBe(testDuration);
      done();
    });

    service.info(testMessage, testDuration);
  });

  it('should emit warning toast message with default duration', (done) => {
    const testMessage = 'Warning message';

    service.toasts$.subscribe((toast: ToastMessage) => {
      expect(toast.type).toBe('warning');
      expect(toast.message).toBe(testMessage);
      expect(toast.duration).toBe(4000);
      done();
    });

    service.warning(testMessage);
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
