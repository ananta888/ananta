// Separate scoped workspace admission from the HTTP transport and runtime pin.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
export const workspaceDigest = (value) => crypto.createHash("sha256").update(
  canonical(value).replace(/[\u007f-\uffff]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`)
).digest("hex");
const digest = workspaceDigest;
const denied = new Set([".git", ".ssh", ".env", ".npmrc", ".netrc", "secrets", "credentials", "id_rsa", "id_ed25519"]);

function validateFiles(files) {
  if (!files || typeof files !== "object" || Array.isArray(files) ||
      Object.keys(files).length < 1 || Object.keys(files).length > 128) {
    throw new Error("evolver_snapshot_files_invalid");
  }
  let bytes = 0;
  const sorted = Object.create(null);
  for (const name of Object.keys(files).sort()) {
    const parts = name.split("/");
    if (name.length > 240 || name.includes("\\") || name.includes("\0") || name.includes(":") ||
        path.posix.isAbsolute(name) || parts.some((p) => !p || p === "." || p === ".." ||
          denied.has(p.toLowerCase()) || p.toLowerCase().startsWith(".env.")) ||
        typeof files[name] !== "string") {
      throw new Error("evolver_snapshot_path_denied");
    }
    bytes += Buffer.byteLength(files[name]);
    if (bytes > 512 * 1024) throw new Error("evolver_snapshot_size_exceeded");
    sorted[name] = files[name];
  }
  return sorted;
}

export function prepareWorkspace(context, git = execFileSync) {
  const snapshot = context?.constraints?.repository_snapshot;
  let files, originRevision = null, scopeDigest = null, mode = "synthetic_context_only";
  if (snapshot !== undefined) {
    if (!snapshot || snapshot.schema !== "ananta.evolver-workspace.v1" ||
        !/^[a-f0-9]{40}$/.test(snapshot.origin_revision) ||
        typeof snapshot.tenant_id !== "string" || !snapshot.tenant_id || snapshot.tenant_id.length > 200 ||
        typeof snapshot.project_id !== "string" || !snapshot.project_id || snapshot.project_id.length > 200) {
      throw new Error("evolver_snapshot_binding_invalid");
    }
    files = validateFiles(snapshot.files);
    const binding = { origin_revision: snapshot.origin_revision, tenant_id: snapshot.tenant_id,
                      project_id: snapshot.project_id, files };
    if (snapshot.content_digest !== digest(binding)) throw new Error("evolver_snapshot_content_mismatch");
    originRevision = snapshot.origin_revision;
    scopeDigest = digest({ tenant_id: snapshot.tenant_id, project_id: snapshot.project_id });
    mode = "scoped_snapshot";
  } else {
    // Compatibility is explicit: this is a synthetic context repo, never a
    // claim to have checked out or analyzed the active project repository.
    files = { "ananta-context.json": JSON.stringify(context ?? {}, null, 2) };
  }
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "evolver-run-"));
  const env = { PATH: "/usr/local/bin:/usr/bin:/bin", LANG: "C.UTF-8", HOME: root,
    GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_NOSYSTEM: "1", GIT_TERMINAL_PROMPT: "0",
    GIT_AUTHOR_NAME: "Ananta Scoped Snapshot", GIT_AUTHOR_EMAIL: "snapshot@localhost",
    GIT_COMMITTER_NAME: "Ananta Scoped Snapshot", GIT_COMMITTER_EMAIL: "snapshot@localhost",
    GIT_AUTHOR_DATE: "2000-01-01T00:00:00Z", GIT_COMMITTER_DATE: "2000-01-01T00:00:00Z" };
  const cleanup = () => fs.rmSync(root, { recursive: true, force: true });
  try {
    for (const [name, text] of Object.entries(files)) {
      const target = path.join(root, name);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, text, { encoding: "utf8", mode: 0o600 });
    }
    const options = { cwd: root, env, encoding: "utf8", timeout: 5000, maxBuffer: 16384 };
    git("git", ["init", "--quiet"], options);
    git("git", ["-c", "core.hooksPath=/dev/null", "add", "--", "."], options);
    git("git", ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false", "commit", "--quiet", "-m",
                `Ananta scoped input ${digest(files)}`], options);
    const executionRevision = git("git", ["rev-parse", "HEAD"], options).trim();
    if (snapshot?.execution_revision && snapshot.execution_revision !== executionRevision) {
      throw new Error("evolver_snapshot_execution_revision_mismatch");
    }
    return { root, cleanup, metadata: { mode, origin_revision: originRevision,
      execution_revision: executionRevision, content_digest: digest(files), scope_digest: scopeDigest,
      evidence_status: "unverified", source_identity_issued: false } };
  } catch (error) {
    cleanup();
    throw error;
  }
}
