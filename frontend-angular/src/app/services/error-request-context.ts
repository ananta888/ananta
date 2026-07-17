import { HttpContextToken } from '@angular/common/http';

/** Requests whose failures are presented and recovered by their caller skip the global error toast. */
export const SUPPRESS_GLOBAL_ERROR_NOTIFICATION = new HttpContextToken<boolean>(() => false);

/**
 * Requests for an ephemeral resource may treat a concurrent 404 as successful cleanup.
 * Other response statuses from the same request remain globally visible.
 */
export const SUPPRESS_GLOBAL_NOT_FOUND_NOTIFICATION = new HttpContextToken<boolean>(() => false);
