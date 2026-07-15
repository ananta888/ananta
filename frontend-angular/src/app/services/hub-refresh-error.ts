/** A local precondition that definitively prevents Hub token refresh. */
export class HubRefreshTerminalError extends Error {}

/** A late refresh result was fenced by logout or a newer login. */
export class HubRefreshSupersededError extends Error {}

export function isDefinitiveHubRefreshFailure(error: unknown): boolean {
  if (error instanceof HubRefreshTerminalError) return true;
  const candidate = error as any;
  const status = Number(
    candidate?.status
    ?? candidate?.error?.status
    ?? candidate?.error?.data?.status
    ?? 0,
  );
  return status === 400 || status === 401;
}
