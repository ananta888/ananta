import { Observable, map } from 'rxjs';

export interface ApiEnvelope<T> {
  status: string;
  data: T;
  [key: string]: unknown;
}

export type ApiResponse<T> = T | ApiEnvelope<T>;

export function isApiEnvelope<T>(response: ApiResponse<T> | unknown): response is ApiEnvelope<T> {
  return Boolean(
    response
    && typeof response === 'object'
    && 'status' in response
    && 'data' in response,
  );
}

export function unwrapApiEnvelope<T>(response: ApiResponse<T>): T {
  return isApiEnvelope<T>(response) ? response.data : response;
}

export function unwrapApiResponse<T>(obs: Observable<ApiResponse<T>>): Observable<T> {
  return obs.pipe(map((response) => unwrapApiEnvelope<T>(response)));
}
