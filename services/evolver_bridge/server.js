import express from "express";
import fs from "fs";
import path from "path";
import { execFile } from "child_process";
import { promisify } from "util";
import { validateRuntime } from "./runtime.js";
import { prepareWorkspace } from "./workspace.js";
import { collectGepArtifacts, GepCompatibilityError } from "./gep_artifacts.js";

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
  const gepDir = path.join(workdir, "gep-assets");
  try {
    fs.mkdirSync(memoryDir, { recursive: true });
    fs.mkdirSync(gepDir, { recursive: true });
    const started = Date.now();
    const baselineProbe = await execFileAsync("node", ["-e",
      `const a=require('${EVOLVER_SRC}/src/gep/assetStore.js');console.log(JSON.stringify([...a.loadGenesReadOnly(),...a.loadCapsulesReadOnly()].map(x=>x.id)));`], {
      cwd: workdir, timeout: Math.min(EVOLVER_TIMEOUT_MS, 5000), maxBuffer: 32768,
      env: {PATH: "/usr/local/bin:/usr/bin:/bin", HOME: workdir, OPENCLAW_WORKSPACE: workdir, GEP_ASSETS_DIR: gepDir}
    });
    const baselineIds = JSON.parse(baselineProbe.stdout);
    if (!Array.isArray(baselineIds) || baselineIds.length > 256 ||
        baselineIds.some(id => typeof id !== "string" || id.length > 200)) {
      throw new GepCompatibilityError("evolver_gep_baseline_incompatible");
    }

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
        timeout: Math.max(1, EVOLVER_TIMEOUT_MS - (Date.now() - started)),
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
          MEMORY_DIR: memoryDir,
          GEP_ASSETS_DIR: gepDir
        },
        maxBuffer: 1024 * 1024 * 4
      }
    );

    const combined = [stdout || "", stderr || ""].join("\n").trim();
    const assets = collectGepArtifacts(gepDir, {baselineIds, memoryDir});
    const structured = assets.filter(a => a.proposal_candidate);

    return res.json({
      id: `evolver-${Date.now()}`,
      status: "completed",
      summary: "Evolver analysis completed",
      provider_metadata: { runtime, workspace: workspace.metadata,
        output_mode: structured.length ? "structured_gep" : "degraded_stdout_compatibility" },
      assets,
      proposals: structured.length ? structured.map(asset => ({
        id: asset.provider_asset_id, kind: asset.kind === "GepPrompt" ? "gep_prompt" : asset.kind.toLowerCase(),
        title: `Evolver ${asset.kind}: ${asset.provider_asset_id}`,
        description: String(asset.raw_payload.summary || asset.raw_payload.text || asset.provider_asset_id),
        risk_level: "unknown", requires_review: true, provider_asset_id: asset.provider_asset_id
      })) : [
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
      reason: err instanceof GepCompatibilityError ? "evolver_gep_compatibility_failed" : "evolver_execution_failed"
    });
  } finally {
    workspace.cleanup();
  }
});

app.listen(PORT, () => {
  console.log(`evolver-bridge listening on ${PORT}`);
});
