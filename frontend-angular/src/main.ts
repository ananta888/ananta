import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient, withInterceptorsFromDi, HTTP_INTERCEPTORS } from '@angular/common/http';
import { provideRouter } from '@angular/router';
import { provideAnimations } from '@angular/platform-browser/animations';
import { AppComponent } from './app/app.component';
import { routes } from './app/app.routes';
import { ErrorHandler } from '@angular/core';
import { GlobalErrorHandler } from './app/services/global-error-handler';
import { AuthInterceptor } from './app/services/auth.interceptor';
import { ErrorInterceptor } from './app/services/error.interceptor';
import { identityRestoreInitializer } from './app/init/identity-restore.initializer';
import { authRequiredRouterInitializer } from './app/init/auth-required-router.initializer';

bootstrapApplication(AppComponent, {
  providers: [
    provideRouter(routes),
    provideHttpClient(withInterceptorsFromDi()),
    provideAnimations(),
    { provide: ErrorHandler, useClass: GlobalErrorHandler },
    { provide: HTTP_INTERCEPTORS, useClass: AuthInterceptor, multi: true },
    { provide: HTTP_INTERCEPTORS, useClass: ErrorInterceptor, multi: true },
    identityRestoreInitializer,
    authRequiredRouterInitializer,
  ]
}).catch(err => console.error(err));
