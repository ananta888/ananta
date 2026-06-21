import { CanActivateFn, Router } from '@angular/router';
import { inject } from '@angular/core';
import { PolicyService } from '../services/policy.service';

/**
 * Guard für Routen, die aktiven Write-Modus erfordern.
 *
 * Leitet bei inaktivem Write-Modus auf /codehug zurück und übergibt
 * `writeRequired=1` + `from=<ursprüngliche URL>` als Query-Params,
 * damit der Shell den Nutzer über die fehlende Berechtigung informieren kann.
 *
 * Verwendung in codehug.routes.ts:
 *   canActivate: [codeHugWriteModeGuard]
 */
export const codeHugWriteModeGuard: CanActivateFn = (_route, state) => {
  const policy = inject(PolicyService);
  const router = inject(Router);

  if (!policy.ensureWriteModeValid()) {
    return router.createUrlTree(['/codehug'], {
      queryParams: { writeRequired: '1', from: state.url },
    });
  }
  return true;
};
