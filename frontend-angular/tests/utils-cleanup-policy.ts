import { type APIRequestContext } from '@playwright/test';

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function apiRequestWithRetry(
  method: 'GET' | 'POST' | 'DELETE' | 'PATCH',
  url: string,
  token: string | null,
  body?: any,
  attempts = 5,
): Promise<Response | null> {
  let lastError: unknown;
  for (let i = 0; i < attempts; i += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const res = await fetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: method === 'GET' || method === 'DELETE' ? undefined : JSON.stringify(body ?? {}),
        signal: controller.signal,
      });
      return res;
    } catch (err) {
      lastError = err;
      if (i < attempts - 1) {
        await sleep(300 * (i + 1));
      }
    } finally {
      clearTimeout(timer);
    }
  }
  console.warn(`apiRequestWithRetry failed for ${method} ${url}: ${String((lastError as any)?.message || lastError)}`);
  return null;
}

export type JourneyCleanupPolicy = {
  trackTemplate: (id: string | null | undefined) => void;
  trackBlueprint: (id: string | null | undefined) => void;
  trackTeam: (id: string | null | undefined) => void;
  trackTask: (id: string | null | undefined) => void;
  trackTasks: (ids: Array<string | null | undefined>) => void;
  run: () => Promise<void>;
};

export function createJourneyCleanupPolicy(
  hubUrl: string,
  token: string | null,
  requestContext?: APIRequestContext,
): JourneyCleanupPolicy {
  const templateIds = new Set<string>();
  const blueprintIds = new Set<string>();
  const teamIds = new Set<string>();
  const taskIds = new Set<string>();

  const track = (set: Set<string>, id: string | null | undefined) => {
    const value = String(id || '').trim();
    if (value) set.add(value);
  };

  const requestJson = async (
    method: 'GET' | 'POST' | 'DELETE',
    url: string,
    data?: any,
    attempts = 3,
  ): Promise<{ status: number; ok: boolean }> => {
    for (let i = 0; i < attempts; i += 1) {
      if (!requestContext) {
        const res = await apiRequestWithRetry(method, url, token, data);
        return { status: res?.status || 0, ok: !!res?.ok };
      }
      try {
        const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
        if (method === 'GET') {
          const res = await requestContext.get(url, { headers, timeout: 20_000 });
          return { status: res.status(), ok: res.ok() };
        }
        if (method === 'POST') {
          const res = await requestContext.post(url, { headers, data, timeout: 20_000 });
          return { status: res.status(), ok: res.ok() };
        }
        const res = await requestContext.delete(url, { headers, timeout: 20_000 });
        return { status: res.status(), ok: res.ok() || res.status() === 404 };
      } catch {
        if (i < attempts - 1) {
          await sleep(250 * (i + 1));
          continue;
        }
        // Fallback path via fetch helper.
        const res = await apiRequestWithRetry(method, url, token, data);
        return { status: res?.status || 0, ok: !!res?.ok };
      }
    }
    return { status: 0, ok: false };
  };

  return {
    trackTemplate: (id) => track(templateIds, id),
    trackBlueprint: (id) => track(blueprintIds, id),
    trackTeam: (id) => track(teamIds, id),
    trackTask: (id) => track(taskIds, id),
    trackTasks: (ids) => ids.forEach((id) => track(taskIds, id)),
    run: async () => {
      const deleteTeams = async () => {
        for (const id of [...teamIds]) {
          // Avoid FK violations from team_members by clearing members explicitly first.
          if (requestContext) {
            try {
              const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
              await requestContext.patch(`${hubUrl}/teams/${id}`, { headers, data: { members: [] }, timeout: 20_000 });
            } catch {}
          } else {
            await apiRequestWithRetry('PATCH', `${hubUrl}/teams/${id}`, token, { members: [] }, 2);
          }

          const res = await requestJson('DELETE', `${hubUrl}/teams/${id}`, undefined, 4);
          if (![200, 204, 404].includes(res.status) && !res.ok) {
            console.warn(`cleanup warning: DELETE /teams/${id} -> ${res.status || 'no-response'}`);
          }
        }
      };

      const tasks = [...taskIds];
      if (tasks.length > 0) {
        const cleanupRes = await requestJson('POST', `${hubUrl}/tasks/cleanup`, { mode: 'delete', task_ids: tasks });
        if (!cleanupRes.ok) {
          console.warn(`cleanup warning: /tasks/cleanup returned ${cleanupRes.status || 'no-response'}`);
        }
      }

      await deleteTeams();

      for (const id of [...blueprintIds]) {
        const res = await requestJson('DELETE', `${hubUrl}/teams/blueprints/${id}`);
        if (![200, 204, 404].includes(res.status) && !res.ok) {
          console.warn(`cleanup warning: DELETE /teams/blueprints/${id} -> ${res.status || 'no-response'}`);
        }
      }

      // Retry team delete once more after blueprint cleanup to reduce FK race leftovers.
      await deleteTeams();

      for (const id of [...templateIds]) {
        const res = await requestJson('DELETE', `${hubUrl}/templates/${id}`, undefined, 4);
        if (![200, 204, 404].includes(res.status) && !res.ok) {
          console.warn(`cleanup warning: DELETE /templates/${id} -> ${res.status || 'no-response'}`);
        }
      }
    },
  };
}

