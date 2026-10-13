import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { execFileSync } from "node:child_process";
import { prepareWorkspace, workspaceDigest } from "../../services/evolver_bridge/workspace.js";

function snapshot(files = { "src/example.py": "print('synthetic snapshot')\n" }) {
  const binding = { origin_revision: "1".repeat(40), tenant_id: "synthetic-tenant", project_id: "synthetic-project", files };
  return { schema: "ananta.evolver-workspace.v1", ...binding,
    content_digest: workspaceDigest(binding) };
}

test("workspace digests match Hub Python canonical JSON for Unicode and key order", () => {
  assert.equal(workspaceDigest({ z: "ä\n", a: "😀" }), "e290b81ae8e133a867db05f914517167431b3e23c83292de683639d53a1a16cb");
  assert.equal(workspaceDigest({ z: "ä\n", a: "😀" }), workspaceDigest({ a: "😀", z: "ä\n" }));
});

test("synthetic context fallback is a valid Git repo without claiming project checkout", () => {
  const workspace = prepareWorkspace({ objective: "synthetic fixture" });
  try {
    assert.match(workspace.metadata.execution_revision, /^[a-f0-9]{40}$/);
    assert.equal(workspace.metadata.mode, "synthetic_context_only");
    assert.equal(workspace.metadata.evidence_status, "unverified");
    assert.equal(execFileSync("git", ["rev-parse", "--is-inside-work-tree"], { cwd: workspace.root, encoding: "utf8" }).trim(), "true");
  } finally { workspace.cleanup(); }
  assert.equal(fs.existsSync(workspace.root), false);
});

test("scoped snapshots are reproducible, isolated and support real diff operations", () => {
  const context = { constraints: { repository_snapshot: snapshot() } };
  const left = prepareWorkspace(context), right = prepareWorkspace(context);
  try {
    assert.equal(left.metadata.execution_revision, right.metadata.execution_revision);
    assert.equal(left.metadata.mode, "scoped_snapshot");
    assert.equal(left.metadata.origin_revision, "1".repeat(40));
    fs.writeFileSync(left.root + "/src/example.py", "changed only inside this workspace\n");
    const diff = execFileSync("git", ["diff", "--name-only"], { cwd: left.root, encoding: "utf8" });
    assert.equal(diff.trim(), "src/example.py");
    assert.equal(fs.readFileSync(right.root + "/src/example.py", "utf8"), "print('synthetic snapshot')\n");
  } finally { left.cleanup(); right.cleanup(); }
});

for (const name of ["../escape", "/etc/passwd", ".git/config", ".env", "src/.env.local", ".ssh/id_rsa", "secrets/key"]) {
  test(`snapshot rejects ${name} before creating workspace`, () => {
    assert.throws(() => prepareWorkspace({ constraints: { repository_snapshot: snapshot({ [name]: "denied" }) } }), /path_denied/);
  });
}

test("content and expected execution revision bindings fail closed", () => {
  const input = snapshot();
  assert.throws(() => prepareWorkspace({ constraints: { repository_snapshot: { ...input, content_digest: "a".repeat(64) } } }), /content_mismatch/);
  assert.throws(() => prepareWorkspace({ constraints: { repository_snapshot: { ...input, execution_revision: "a".repeat(40) } } }), /execution_revision_mismatch/);
});

test("Git failure cleans up temporary input state", () => {
  let root;
  assert.throws(() => prepareWorkspace({ objective: "synthetic failure" }, (_cmd, _args, options) => {
    root = options.cwd;
    throw new Error("synthetic Git failure");
  }), /synthetic Git failure/);
  assert.equal(fs.existsSync(root), false);
});
