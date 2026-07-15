import { HttpContextToken } from '@angular/common/http';

/** Requests carrying refresh credentials bypass access-token authentication. */
export const SKIP_ACCESS_TOKEN_AUTH = new HttpContextToken<boolean>(() => false);
