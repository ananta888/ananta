import { HTTP_INTERCEPTORS, provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';
import { ErrorHandler } from '@angular/core';
import { provideAnimations } from '@angular/platform-browser/animations';
import { bootstrapApplication } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';

import { AppComponent } from './app/app.component';
import { routes } from './app/app.routes';
import { authRequiredRouterInitializer } from './app/init/auth-required-router.initializer';
import { identityRestoreInitializer } from './app/init/identity-restore.initializer';
import { AuthInterceptor } from './app/services/auth.interceptor';
import { ErrorInterceptor } from './app/services/error.interceptor';
import { GlobalErrorHandler } from './app/services/global-error-handler';
import { provideSfuProjectionSignatureVerifier } from './app/services/sfu-projection-signature-verifier.service';
import { SourceControlProjectInterceptor } from './app/services/source-control-project.interceptor';
import { SourceControlHubInterceptor } from './app/services/source-control-hub.interceptor';

/**
 * Shared application bootstrap. Production and the explicit live-E2E entry use
 * the same providers; only the latter installs browser evidence adapters.
 */
export function bootstrapAnantaApplication() {
  return bootstrapApplication(AppComponent, {
    providers: [
      provideRouter(routes),
      provideHttpClient(withInterceptorsFromDi()),
      provideAnimations(),
      { provide: ErrorHandler, useClass: GlobalErrorHandler },
      { provide: HTTP_INTERCEPTORS, useClass: SourceControlHubInterceptor, multi: true },
      { provide: HTTP_INTERCEPTORS, useClass: AuthInterceptor, multi: true },
      { provide: HTTP_INTERCEPTORS, useClass: ErrorInterceptor, multi: true },
      { provide: HTTP_INTERCEPTORS, useClass: SourceControlProjectInterceptor, multi: true },
      identityRestoreInitializer,
      authRequiredRouterInitializer,
      provideSfuProjectionSignatureVerifier(),
    ],
  });
}
