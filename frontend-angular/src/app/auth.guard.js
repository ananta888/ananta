import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { UserAuthService } from './services/user-auth.service';
export const authGuard = () => {
    const auth = inject(UserAuthService);
    const router = inject(Router);
    if (auth.isLoggedIn()) {
        return true;
    }
    return router.parseUrl('/login');
};
//# sourceMappingURL=auth.guard.js.map