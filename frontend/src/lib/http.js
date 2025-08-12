const baseUrl = (import.meta && import.meta.env && import.meta.env.VITE_API_URL) ? import.meta.env.VITE_API_URL : '';

async function handleResponse(res) {
  // Treat missing res.ok as OK to be compatible with simple fetch mocks in tests
  if (res && 'ok' in res && res.ok === false) {
    let message = `HTTP ${res.status}`;
    try {
      const text = typeof res.text === 'function' ? await res.text() : '';
      if (text) message = text;
    } catch (_) {}
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  // Try JSON, fallback to text
  const ct = res.headers && res.headers.get ? res.headers.get('content-type') : '';
  if (ct && ct.includes('application/json')) {
    return res.json();
  }
  try {
    return await res.json();
  } catch (_) {
    return res.text ? res.text() : null;
  }
}

export async function get(path) {
  // Keep default fetch signature without options to preserve existing tests that expect no opts for GET
  const res = await fetch(`${baseUrl}${path}`);
  return handleResponse(res);
}

export async function post(path, body) {
  const res = await fetch(`${baseUrl}${path}` , {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined
  });
  return handleResponse(res);
}
