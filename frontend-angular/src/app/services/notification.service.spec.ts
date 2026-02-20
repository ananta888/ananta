import { describe, it, expect, beforeEach, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { NotificationService, Notification } from './notification.service';

describe('NotificationService', () => {
  let service: NotificationService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [NotificationService]
    });
    service = TestBed.inject(NotificationService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should emit notification with show method', (done) => {
    const testMessage = 'Test notification';
    const testType = 'info';
    const testDuration = 3000;

    service.notifications$.subscribe((notification: Notification) => {
      expect(notification.message).toBe(testMessage);
      expect(notification.type).toBe(testType);
      expect(notification.duration).toBe(testDuration);
      expect(notification.id).toBeTruthy();
      expect(typeof notification.id).toBe('string');
      done();
    });

    service.show(testMessage, testType, testDuration);
  });

  it('should emit notification with default values', (done) => {
    const testMessage = 'Default notification';

    service.notifications$.subscribe((notification: Notification) => {
      expect(notification.message).toBe(testMessage);
      expect(notification.type).toBe('info');
      expect(notification.duration).toBe(5000);
      done();
    });

    service.show(testMessage);
  });

  it('should emit error notification', (done) => {
    const testMessage = 'Error occurred';

    service.notifications$.subscribe((notification: Notification) => {
      expect(notification.message).toBe(testMessage);
      expect(notification.type).toBe('error');
      expect(notification.duration).toBe(5000);
      done();
    });

    service.error(testMessage);
  });

  it('should emit success notification', (done) => {
    const testMessage = 'Operation successful';

    service.notifications$.subscribe((notification: Notification) => {
      expect(notification.message).toBe(testMessage);
      expect(notification.type).toBe('success');
      expect(notification.duration).toBe(5000);
      done();
    });

    service.success(testMessage);
  });

  it('should emit info notification', (done) => {
    const testMessage = 'Information';

    service.notifications$.subscribe((notification: Notification) => {
      expect(notification.message).toBe(testMessage);
      expect(notification.type).toBe('info');
      expect(notification.duration).toBe(5000);
      done();
    });

    service.info(testMessage);
  });

  it('should generate unique IDs for each notification', () => {
    const notifications: Notification[] = [];

    service.notifications$.subscribe((notification: Notification) => {
      notifications.push(notification);
    });

    service.info('First');
    service.info('Second');
    service.info('Third');

    expect(notifications.length).toBe(3);
    expect(notifications[0].id).not.toBe(notifications[1].id);
    expect(notifications[1].id).not.toBe(notifications[2].id);
    expect(notifications[0].id).not.toBe(notifications[2].id);
  });

  it('should emit multiple notifications with different types', () => {
    const notifications: Notification[] = [];

    service.notifications$.subscribe((notification: Notification) => {
      notifications.push(notification);
    });

    service.error('Error message');
    service.success('Success message');
    service.info('Info message');

    expect(notifications.length).toBe(3);
    expect(notifications[0].type).toBe('error');
    expect(notifications[1].type).toBe('success');
    expect(notifications[2].type).toBe('info');
  });
});
