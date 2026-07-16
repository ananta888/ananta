import { HttpContextToken } from '@angular/common/http';

/** Requests whose failures are presented and recovered by their caller skip the global error toast. */
export const SUPPRESS_GLOBAL_ERROR_NOTIFICATION = new HttpContextToken<boolean>(() => false);
