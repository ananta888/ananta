import express from "express";
import fs from "fs";
import os from "os";
import path from "path";
import { execFile } from "child_process";
import { promisify } from "util";

const execFileAsync = promisify(execFile);
const app = express();

app.use(express.json({ limit: "1mb" }));

const PORT = Number(process.env.PORT || 8080);
const EVOLVER_TIMEOUT_MS = Number(process.env.EVOLVER_TIMEOUT_MS || 120000);
const EVOLVER_SRC = "/opt/evolver-src";

app.get("/health", async (_req, res) => {
  try {
    if (!fs.existsSync(path.join(EVOLVER_SRC, "index.js"))) {
      return res.status(503).json({ status: "unavailable", reason: "evolver_missing" });
    }
    return res.json({ status: "available", checked: true });
  } catch (err) {
    return res.status(500).json({ status: "error", error: String(err) });
  }
});

app.post("/evolution/analyze", async (req, res) => {
  const context = req.body?.context || {};
  const objective = String(context.objective || "Analyze and improve current task").trim();

  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "evolver-run-"));
  const memoryDir = path.join(workdir, "memory");
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

  try {
    const { stdout, stderr } = await execFileAsync(
      "node",
      [path.join(EVOLVER_SRC, "index.js")],
      {
        cwd: workdir,
        timeout: EVOLVER_TIMEOUT_MS,
        env: {
          ...process.env,
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
      error: String(err)
    });
  }
});

app.listen(PORT, () => {
  console.log(`evolver-bridge listening on ${PORT}`);
});
