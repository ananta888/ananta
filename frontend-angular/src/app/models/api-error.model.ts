export interface ApiError {
  code: string;
  message: string;
  details?: any;
  status_code?: number;
}

export interface ValidationError extends ApiError {
  field_errors?: { [field: string]: string[] };
  global_errors?: string[];
}

export function isValidationError(error: any): error is ValidationError {
  return error && (error.field_errors !== undefined || error.global_errors !== undefined);
}
