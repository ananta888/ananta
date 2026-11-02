// Runtime identity validation is separate from transport and workspace creation.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";

const sha256 = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");

export function validateRuntime(root, contract, git = execFileSync) {
  if (contract.schema !== "ananta.evolver-runtime.v1" || !/^[a-f0-9]{40}$/.test(contract.commit) ||
      !/^\d+\.\d+\.\d+$/.test(contract.version) || contract.entrypoint !== "index.js" ||
      contract.analyze_only !== true || !/^[a-f0-9]{64}$/.test(contract.license_sha256) ||
      !/^[a-f0-9]{64}$/.test(contract.package_lock_sha256)) {
    throw new Error("evolver_runtime_contract_invalid");
  }
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
    const options = {
      encoding: "utf8", timeout: 5000, maxBuffer: 4096,
      env: { PATH: "/usr/local/bin:/usr/bin:/bin", LANG: "C.UTF-8", GIT_CONFIG_NOSYSTEM: "1",
             GIT_CONFIG_GLOBAL: "/dev/null", GIT_OPTIONAL_LOCKS: "0" }
    };
    const prefix = ["-c", `safe.directory=${root}`, "-C", root];
    const revision = git("git", [...prefix, "rev-parse", "HEAD"], options).trim();
    git("git", [...prefix, "diff", "--quiet", "HEAD", "--"], options);
    if (revision !== contract.commit || pkg.version !== contract.version || pkg.license !== contract.license ||
        sha256(fs.readFileSync(path.join(root, "LICENSE"))) !== contract.license_sha256 ||
        sha256(fs.readFileSync(path.join(root, "package-lock.json"))) !== contract.package_lock_sha256 ||
        !fs.statSync(path.join(root, contract.entrypoint)).isFile()) {
      throw new Error("evolver_runtime_binding_mismatch");
    }
    return { version: pkg.version, commit: revision, license: pkg.license,
             license_sha256: contract.license_sha256, package_lock_sha256: contract.package_lock_sha256,
             contract_sha256: sha256(Buffer.from(JSON.stringify(contract))),
             protocol_version: contract.protocol_version, analyze_only: true };
  } catch (error) {
    if (error.message === "evolver_runtime_binding_mismatch") throw error;
    throw new Error("evolver_runtime_unavailable");
  }
}
