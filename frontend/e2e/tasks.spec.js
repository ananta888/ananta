import { test, expect } from '@playwright/test';

// Extend timeout for this file to accommodate polling of agent tasks
// Worst-case: 60*2s + 30*1.5s + network overhead (~165s)
// Set to 4 minutes to be safe within CI
test.setTimeout(120000);

// Use Playwright baseURL for controller via request context; only configure agentUrl for direct agent calls inside Docker
const agentUrl = process.env.AGENT_URL || 'http://ai-agent:5000';
// Optionales Flag, um Agent-Verarbeitung zu verifizieren
const verifyAgent = process.env.VERIFY_AGENT !== '0';


/**
 * Prüft, ob Task in Agent-Logs erscheint
 */
async function isTaskInAgentLog(request, agent, task) {
  try {
    const res = await request.get(`${agentUrl}/agent/${encodeURIComponent(agent)}/log`);
    if (!res.ok()) return false;
    const text = await res.text();
    return text.includes(task);
  } catch {
    return false;
  }
}

/**
 * Prüft, ob Task für einen Agent in der Datenbank vorhanden ist
 * - robust gegenüber unterschiedlichen JSON-Formaten:
 *   - Array als Wurzel:            [ { task, agent, ... }, ... ]
 *   - Objekt mit 'tasks'/'items'/'data': { tasks: [...]} / { items: [...] } / { data: [...] }
 * - nutzt relative URL (Request-Context baseURL)
 */
async function isTaskPresentForAgent(request, agent, task) {
  async function fetchTasksFromController(path) {
    const res = await request.get(path);
    if (!res.ok()) return null;
    const json = await res.json();
    if (Array.isArray(json)) return json;
    if (json && Array.isArray(json.tasks)) return json.tasks;
    if (json && Array.isArray(json.items)) return json.items;
    if (json && Array.isArray(json.data)) return json.data;
    return null;
  }

  try {
    // Try primary API path first
    let list = await fetchTasksFromController(`/api/agents/${encodeURIComponent(agent)}/tasks`);
    // Fallback to non-/api alias if necessary
    if (!list) {
      list = await fetchTasksFromController(`/agent/${encodeURIComponent(agent)}/tasks`);
    }
    // Final fallback: ask AI-Agent for its view of controller tasks (read-only)
    if (!list) {
      try {
        const res = await fetch(`${agentUrl}/tasks`);
        if (res.ok) {
          const json = await res.json();
          if (json && Array.isArray(json.tasks)) {
            list = json.tasks;
          }
        }
      } catch {
        // ignore
      }
    }

    if (!list) return false;

    const byAgent = list.filter((t) => !t.agent || t.agent === agent);
    return byAgent.some((t) => {
      const candidates = [t.task, t.description, t.name].filter(Boolean);
      return candidates.includes(task);
    });
  } catch (error) {
    console.error('Fehler beim Prüfen des Tasks in der Datenbank:', error);
    return false;
  }
}

test('Task-Anlage via UI persistiert und wird vom AI-Agent verarbeitet', async ({ page, request }) => {
  const task = `e2e-task-${Date.now()}`;
  const agent = 'Architect';
  let newTaskId = null;

  // 1) UI öffnen und zum Task-Bereich navigieren
  await test.step('UI öffnen und zu Tasks navigieren', async () => {
    await page.goto('/ui/');
    await page.waitForLoadState('domcontentloaded');
    await page.click('text=Tasks');
  });

  // 2) Task anlegen
  await test.step('Task in der UI anlegen', async () => {
    await page.fill('input[placeholder="Task"]', task);
    await page.fill('input[placeholder="Agent (optional)"]', agent);
    // Klicke Add ohne auf ein spezifisches Netzwertereignis zu warten (robuster gegen SW/Fetch-Implementierung)
    await page.click('text=Add');
    // Versuche optional, die neue Task-ID aus der Controller-Liste zu bestimmen
    try {
      const listRes = await request.get(`/api/agents/${encodeURIComponent(agent)}/tasks`);
      if (listRes.ok()) {
        const json = await listRes.json();
        const items = Array.isArray(json) ? json : (json.tasks || json.items || json.data || []);
        const found = items.find((t) => (t.task === task) && (!t.agent || t.agent === agent));
        if (found && found.id) newTaskId = found.id;
      }
    } catch {}
  });

  // 3) Prüfen ob Task in Datenbank persistiert wurde
  await test.step('Prüfen, ob Task in der Datenbank persistiert wurde', async () => {
    await expect
      .poll(
        async () => await isTaskPresentForAgent(request, agent, task),
        { timeout: 20000, intervals: [500, 1000, 2000] }
      )
      .toBe(true);
  });

  // 4) Optional: Verarbeitung durch den AI-Agent prüfen
  if (verifyAgent) {
    await test.step('Prüfen, ob Task vom AI-Agent verarbeitet wurde', async () => {
      await expect
        .poll(
          async () => await isTaskInAgentLog(request, agent, task),
          { timeout: 15000, intervals: [500, 1000, 2000] }
        )
        .toBe(true, `Task "${task}" sollte in den Agent-Logs erscheinen`);
    });
  } else {
    test.info().annotations.push({ 
      type: 'note', 
      description: 'Agent-Verarbeitung wurde übersprungen (VERIFY_AGENT=0)' 
    });
  }

  // 5) Prüfen, ob Task nach Verarbeitung nicht mehr in der Liste ist
  await test.step('Prüfen, ob Task nach Verarbeitung entfernt wurde', async () => {
    // Nur testen, wenn Agent-Verarbeitung geprüft wurde
    if (verifyAgent) {
      await expect
        .poll(
          async () => !(await isTaskPresentForAgent(request, agent, task)),
          { timeout: 8000, intervals: [500, 1000, 2000] }
        )
        .toBe(true, `Task "${task}" sollte nach Verarbeitung nicht mehr in der Liste sein`);
    }
  });

  // 6) Cleanup: Nur die in diesem Test erstellten Daten wieder entfernen
  await test.step('Cleanup: Entferne erzeugten Task', async () => {
    try {
      if (newTaskId) {
        try {
          const del = await request.delete(`/api/tasks/${newTaskId}`);
          if (del.ok()) {
            return;
          }
        } catch {}
      }
      async function fetchList(path) {
        const res = await request.get(path);
        if (!res.ok()) return null;
        const json = await res.json();
        if (Array.isArray(json)) return json;
        if (json && Array.isArray(json.tasks)) return json.tasks;
        if (json && Array.isArray(json.items)) return json.items;
        if (json && Array.isArray(json.data)) return json.data;
        return null;
      }

      let list = await fetchList(`/api/agents/${encodeURIComponent(agent)}/tasks`);
      if (!list) {
        list = await fetchList(`/agent/${encodeURIComponent(agent)}/tasks`);
      }

      if (list && list.length) {
        const found = list.find((t) => (t.task === task) && (t.agent === undefined || t.agent === null || t.agent === agent));
        if (found && found.id) {
          const del = await request.delete(`/api/tasks/${found.id}`);
          if (!del.ok()) {
            console.warn('Cleanup: Löschen des Tasks fehlgeschlagen mit Status', del.status());
          }
        }
      }
    } catch (e) {
      console.warn('Cleanup: Ausnahme beim Entfernen des Tasks', e);
    }
  });
});