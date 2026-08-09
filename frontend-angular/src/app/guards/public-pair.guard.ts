import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { PairPublicAuthorityPolicy } from '../services/pair-public-authority.policy';

/** Allows the public Pair surface only for the pinned, current OIDC authority. */
export const publicPairGuard: CanActivateFn = () => {
  const authority = inject(PairPublicAuthorityPolicy);
  const router = inject(Router);

  return authority.ready
    ? true
    : router.parseUrl('/login?sphere=oidc');
};
