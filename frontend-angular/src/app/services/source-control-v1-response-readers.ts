/**
 * Response readers of the Source Control v1 HTTP client: ETag-bound detail
 * projections, the CodeHug mutation result contract and the mapping of
 * transport failures onto stable client errors.
 */

import { HttpErrorResponse, HttpResponse } from '@angular/common/http';
import { Observable, catchError, throwError } from 'rxjs';

import {
  SourceControlV1ContractError,
  assertSourceControlSha256,
  parseContextPolicyVersion,
  parseSourceControlEnvelope,
  parseSourceControlErrorEnvelope,
  parseSourceControlProjection,
} from '../models/source-control-v1-api.model';
import type {
  ContextPolicyVersionDetail,
  SourceControlProjectionDetail,
} from '../models/source-control-v1-api.model';
import {
  SourceControlV1HttpError,
} from './source-control-v1-api.contracts';
import type {
  CodeHugMutationResult,
} from './source-control-v1-api.contracts';

export function parseCodeHugMutationResult(
  value: unknown,
): CodeHugMutationResult {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new SourceControlV1ContractError(
      'codehug_mutation_result_invalid',
    );
  }
  const result = value as Record<string, unknown>;
  const keys = Object.keys(result).sort();
  const expected = [
    'binding_digest',
    'operation_id',
    'schema',
    'status',
  ];
  if (
    keys.length !== expected.length
    || keys.some((key, index) => key !== expected[index])
    || result['schema'] !== 'ananta.codehug.mutation-result.v1'
    || typeof result['status'] !== 'string'
    || (
      result['operation_id'] !== null
      && typeof result['operation_id'] !== 'string'
    )
    || (
      result['binding_digest'] !== null
      && typeof result['binding_digest'] !== 'string'
    )
  ) {
    throw new SourceControlV1ContractError(
      'codehug_mutation_result_invalid',
    );
  }
  if (typeof result['binding_digest'] === 'string') {
    assertSourceControlSha256(
      result['binding_digest'],
      'binding_digest',
    );
  }
  return result as unknown as CodeHugMutationResult;
}

export function readSourceControlProjectionDetail(
  response: HttpResponse<unknown>,
): SourceControlProjectionDetail {
  const projection = parseSourceControlEnvelope(
    response.body,
    parseSourceControlProjection,
  );
  const header = response.headers.get('ETag');
  if (header === null) {
    throw new SourceControlV1ContractError('etag_header_required');
  }
  const etag = header.trim().replace(/^"|"$/g, '');
  assertSourceControlSha256(etag, 'etag_header');
  if (etag !== projection.etag) {
    throw new SourceControlV1ContractError('etag_header_mismatch');
  }
  return { projection, etag };
}

export function readContextPolicyVersionDetail(
  response: HttpResponse<unknown>,
): ContextPolicyVersionDetail {
  const policy = parseSourceControlEnvelope(
    response.body,
    parseContextPolicyVersion,
  );
  const header = response.headers.get('ETag');
  if (header === null) {
    throw new SourceControlV1ContractError('etag_header_required');
  }
  const etag = header.trim().replace(/^"|"$/g, '');
  assertSourceControlSha256(etag, 'etag_header');
  if (etag !== policy.etag) {
    throw new SourceControlV1ContractError('etag_header_mismatch');
  }
  return { policy, etag };
}

export function handleSourceControlRequest<T>(request: Observable<T>): Observable<T> {
  return request.pipe(
    catchError((error: unknown) =>
      throwError(() => toSourceControlClientError(error)),
    ),
  );
}

export function toSourceControlClientError(error: unknown): Error {
  if (error instanceof SourceControlV1ContractError) {
    return error;
  }
  if (!(error instanceof HttpErrorResponse)) {
    return new SourceControlV1HttpError(0, 'transport_error');
  }
  try {
    const envelope = parseSourceControlErrorEnvelope(error.error);
    return new SourceControlV1HttpError(
      error.status,
      envelope.error.code,
    );
  } catch {
    return new SourceControlV1HttpError(
      error.status,
      'invalid_error_contract',
    );
  }
}
