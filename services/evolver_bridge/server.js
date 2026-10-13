import express from "express";
import fs from "fs";
import path from "path";
import { execFile } from "child_process";
import { promisify } from "util";
import { validateRuntime } from "./runtime.js";
import { prepareWorkspace } from "./workspace.js";

const execFileAsync = promisify(execFile);
const app = express();

app.use(express.json({ limit: "1mb" }));

const PORT = Number(process.env.PORT || 8080);
const timeout = Number(process.env.EVOLVER_TIMEOUT_MS || 120000);
if (!Number.isInteger(timeout) || timeout < 100 || timeout > 120000) {
  throw new Error("evolver_timeout_policy_invalid");
}
const EVOLVER_TIMEOUT_MS = timeout;
const EVOLVER_SRC = "/opt/evolver-src";
const RUNTIME_CONTRACT = JSON.parse(fs.readFileSync(new URL("./runtime-contract.json", import.meta.url), "utf8"));

app.get("/health", async (_req, res) => {
  try {
    const runtime = validateRuntime(EVOLVER_SRC, RUNTIME_CONTRACT);
    return res.json({ status: "available", checked: true, runtime });
  } catch (err) {
    return res.status(503).json({ status: "unavailable", checked: true, reason: err.message });
  }
});

app.post("/evolution/analyze", async (req, res) => {
  let runtime;
  try {
    runtime = validateRuntime(EVOLVER_SRC, RUNTIME_CONTRACT);
  } catch (err) {
    return res.status(503).json({ status: "failed", proposals: [], reason: err.message });
  }
  const context = req.body?.context || {};
  const objective = String(context.objective || "Analyze and improve current task").trim();

  let workspace;
  try {
    workspace = prepareWorkspace(context);
  } catch (err) {
    return res.status(422).json({ status: "failed", proposals: [], reason: "evolver_workspace_admission_failed" });
  }
  const workdir = workspace.root;
  const memoryDir = path.join(workdir, "memory");
  try {
    fs.mkdirSync(memoryDir, { recursive: true });

    const signals = {
      objective,
      task_id: context.task_id || null,
      trace_id: context.trace_id || null,
      task: context.task || null,
      verification: context.verification || null,
      artifacts: context.artifacts || null,
      constraints: context.constraints || null
    };

    fs.writeFileSync(
      path.join(memoryDir, "ananta-context.json"),
      JSON.stringify(signals, null, 2),
      "utf-8"
    );

    const { stdout, stderr } = await execFileAsync(
      "node",
      [path.join(EVOLVER_SRC, "index.js"), "run"],
      {
        cwd: workdir,
        timeout: EVOLVER_TIMEOUT_MS,
        env: {
          PATH: "/usr/local/bin:/usr/bin:/bin",
          LANG: "C.UTF-8",
          HOME: workdir,
          GIT_CONFIG_NOSYSTEM: "1",
          GIT_CONFIG_GLOBAL: "/dev/null",
          OPENCLAW_WORKSPACE: workdir,
          EVOLVER_LOGS_DIR: path.join(workdir, "logs"),
          // Credentials and external Hub configuration are never inherited.
          A2A_HUB_URL: "http://127.0.0.1:9",
          EVOMAP_HUB_URL: "http://127.0.0.1:9",
          MEMORY_DIR: memoryDir
        },
        maxBuffer: 1024 * 1024 * 4
      }
    );

    const combined = [stdout || "", stderr || ""].join("\n").trim();

    return res.json({
      id: `evolver-${Date.now()}`,
      status: "completed",
      summary: "Evolver analysis completed",
      provider_metadata: { runtime, workspace: workspace.metadata, output_mode: "degraded_stdout_compatibility" },
      proposals: [
        {
          id: `prompt-${Date.now()}`,
          kind: "prompt",
          title: "Evolver generated improvement prompt",
          description: combined.slice(0, 12000) || "Evolver completed without textual output.",
          risk_level: "medium",
          requires_review: true
        }
      ]
    });
  } catch (err) {
    return res.status(500).json({
      status: "failed",
      summary: "Evolver execution failed",
      proposals: [],
      validation_results: [],
      reason: "evolver_execution_failed"
    });
  } finally {
    workspace.cleanup();
  }
});

app.listen(PORT, () => {
  console.log(`evolver-bridge listening on ${PORT}`);
});
