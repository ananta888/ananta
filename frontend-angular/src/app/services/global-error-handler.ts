import { ErrorHandler, Injectable, Injector, inject } from '@angular/core';
import { NotificationService } from './notification.service';
import { HttpErrorResponse } from '@angular/common/http';

@Injectable()
export class GlobalErrorHandler implements ErrorHandler {
  private injector = inject(Injector);


  handleError(error: any) {
    const ns = this.injector.get(NotificationService);
    
    let message = 'Ein unerwarteter Fehler ist aufgetreten.';
    
    if (error instanceof HttpErrorResponse) {
      message = `API-Fehler: ${error.status} ${error.statusText}`;
      if (error.error?.error) {
        message += ` - ${error.error.error}`;
      } else if (error.error?.detail) {
        message += ` - ${error.error.detail}`;
      } else if (error.error?.message) {
        message += ` - ${error.error.message}`;
      } else if (typeof error.error === 'string' && error.error.length < 100) {
        message += ` - ${error.error}`;
      }
    } else if (error instanceof Error) {
      message = error.message;
    } else if (typeof error === 'string') {
      message = error;
    }

    console.error('Global Error Handler:', error);
    ns.error(message);
  }
}
