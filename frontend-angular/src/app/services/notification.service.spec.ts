import { describe, it, expect, beforeEach } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
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

  it('should emit notification with show method', async () => {
    const testMessage = 'Test notification';
    const testType = 'info';
    const testDuration = 3000;
    const notificationPromise = firstValueFrom(service.notifications$);
    service.show(testMessage, testType, testDuration);
    const notification: Notification = await notificationPromise;
    expect(notification.message).toBe(testMessage);
    expect(notification.type).toBe(testType);
    expect(notification.duration).toBe(testDuration);
    expect(notification.id).toBeTruthy();
    expect(typeof notification.id).toBe('string');
  });

  it('should emit notification with default values', async () => {
    const testMessage = 'Default notification';
    const notificationPromise = firstValueFrom(service.notifications$);
    service.show(testMessage);
    const notification: Notification = await notificationPromise;
    expect(notification.message).toBe(testMessage);
    expect(notification.type).toBe('info');
    expect(notification.duration).toBe(5000);
  });

  it('should emit error notification', async () => {
    const testMessage = 'Error occurred';
    const notificationPromise = firstValueFrom(service.notifications$);
    service.error(testMessage);
    const notification: Notification = await notificationPromise;
    expect(notification.message).toBe(testMessage);
    expect(notification.type).toBe('error');
    expect(notification.duration).toBe(5000);
  });

  it('should emit success notification', async () => {
    const testMessage = 'Operation successful';
    const notificationPromise = firstValueFrom(service.notifications$);
    service.success(testMessage);
    const notification: Notification = await notificationPromise;
    expect(notification.message).toBe(testMessage);
    expect(notification.type).toBe('success');
    expect(notification.duration).toBe(5000);
  });

  it('should emit info notification', async () => {
    const testMessage = 'Information';
    const notificationPromise = firstValueFrom(service.notifications$);
    service.info(testMessage);
    const notification: Notification = await notificationPromise;
    expect(notification.message).toBe(testMessage);
    expect(notification.type).toBe('info');
    expect(notification.duration).toBe(5000);
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
