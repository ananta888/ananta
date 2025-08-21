import { test, expect } from '@playwright/test';

// Extend timeout for this file to accommodate polling of agent tasks
// Worst-case: 60*2s + 30*1.5s + network overhead (~165s)
// Set to 4 minutes to be safe within CI
test.setTimeout(240000);

// Use Playwright baseURL for controller via request context; only configure agentUrl for direct agent calls inside Docker
const agentUrl = process.env.AGENT_URL || 'http://ai-agent:5000';
// Optionales Flag, um Agent-Verarbeitung zu verifizieren
const verifyAgent = process.env.VERIFY_AGENT !== '0';

/**
 * Hilfsfunktion: prüft via Controller-API, ob Task für Agent vorhanden ist
 */
async function isTaskPresentForAgent(request, agent, task) {
  const res = await request.get(`/agent/${encodeURIComponent(agent)}/tasks`);
  if (!res.ok()) return false;
  const data = await res.json();
  const tasks = (data || {}).tasks || [];
  return tasks.some((t) => 
    typeof t === 'string' ? t === task : (t && t.task === task)
  );
}

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

test('Task-Anlage via UI persistiert und wird vom AI-Agent verarbeitet', async ({ page, request }) => {
  const task = `e2e-task-${Date.now()}`;
  const agent = 'Architect';

  // 1) UI öffnen und zum Task-Bereich navigieren
  await test.step('UI öffnen und zu Tasks navigieren', async () => {
    await page.goto('/ui/');
    await page.waitForLoadState('networkidle');
    await page.click('text=Tasks');
  });

  // 2) Task anlegen
  await test.step('Task in der UI anlegen', async () => {
    await page.fill('input[placeholder="Task"]', task);
    await page.fill('input[placeholder="Agent (optional)"]', agent);
    await page.click('text=Add');
  });

  // 3) Prüfen ob Task in Datenbank persistiert wurde
  await test.step('Prüfen, ob Task in der Datenbank persistiert wurde', async () => {
    await expect
      .poll(
        async () => await isTaskPresentForAgent(request, agent, task),
        { timeout: 120000, intervals: [1000, 2000, 5000] }
      )
      .toBe(true, `Task "${task}" sollte für Agent "${agent}" in der Datenbank zu finden sein`);
  });

  // 4) Optional: Verarbeitung durch den AI-Agent prüfen
  if (verifyAgent) {
    await test.step('Prüfen, ob Task vom AI-Agent verarbeitet wurde', async () => {
      await expect
        .poll(
          async () => await isTaskInAgentLog(request, agent, task),
          { timeout: 45000, intervals: [1000, 1500, 3000] }
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
          { timeout: 10000, intervals: [1000, 2000] }
        )
        .toBe(true, `Task "${task}" sollte nach Verarbeitung nicht mehr in der Liste sein`);
    }
  });
});
