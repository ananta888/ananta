/** Enforce the already-selected model endpoint at the SDK HTTP boundary. */
export function createPiFetch({ baseUrl, modelId, maxTokens, routingPolicy = null,
  fetchImplementation = globalThis.fetch }) {
  const deny = () => { throw new Error("pi_http_request_not_authorized"); };
  const strictRouting = value => value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === 2 && value.allow_fallbacks === false && value.require_parameters === true;
  const requiresRouting = routingPolicy !== null;
  if (requiresRouting && !strictRouting(routingPolicy)) deny();
  let endpoint;
  try {
    const base = new URL(baseUrl);
    if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password
        || base.search || base.hash || !Number.isInteger(maxTokens) || maxTokens < 1
        || typeof modelId !== "string" || !modelId) deny();
    endpoint = base.href.replace(/\/+$/, "") + "/chat/completions";
  } catch { deny(); }
  let attempted = false;
  return async (input, init = {}) => {
    // Count attempts, not successful responses: SDK retries cannot spend again.
    if (attempted) deny();
    attempted = true;
    // The pinned SDK uses URL/string plus an explicit JSON body. Refuse opaque
    // Request/stream bodies instead of reading unbounded or one-shot streams.
    if (!(typeof input === "string" || input instanceof URL)
        || String(input) !== endpoint || init.method !== "POST"
        || typeof init.body !== "string" || init.body.length > 2_000_000) deny();
    let body;
    try { body = JSON.parse(init.body); } catch { deny(); }
    if (!body || body.model !== modelId || body.stream !== true
        || (body.tools !== undefined && (!Array.isArray(body.tools) || body.tools.length))
        || body.functions !== undefined || body.function_call !== undefined
        || (body.tool_choice !== undefined && body.tool_choice !== "none")
        || body.max_tokens !== maxTokens || body.max_completion_tokens !== undefined
        || body.models !== undefined || body.route !== undefined
        || (requiresRouting ? !strictRouting(body.provider) : body.provider !== undefined)) deny();
    const response = await fetchImplementation(input, { ...init, redirect: "error" });
    if (response.redirected || (response.status >= 300 && response.status < 400)
        || (response.url && response.url !== endpoint)) {
      // Do not wait for a remote response body while refusing the destination.
      response.body?.cancel().catch(() => {});
      deny();
    }
    return response;
  };
}
