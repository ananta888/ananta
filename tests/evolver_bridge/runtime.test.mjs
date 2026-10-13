import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { validateRuntime } from "../../services/evolver_bridge/runtime.js";

const sha256 = (text) => crypto.createHash("sha256").update(text).digest("hex");

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ananta-runtime-test-"));
  const contract = { schema: "ananta.evolver-runtime.v1", version: "1.2.3", commit: "1".repeat(40),
    license: "TEST-ONLY", license_sha256: sha256("synthetic test license"),
    package_lock_sha256: sha256("synthetic lock"), entrypoint: "index.js", analyze_only: true,
    protocol_version: "synthetic-v1" };
  fs.writeFileSync(path.join(root, "package.json"), JSON.stringify({ version: "1.2.3", license: "TEST-ONLY" }));
  fs.writeFileSync(path.join(root, "LICENSE"), "synthetic test license");
  fs.writeFileSync(path.join(root, "package-lock.json"), "synthetic lock");
  fs.writeFileSync(path.join(root, "index.js"), "// synthetic runtime, never executed");
  return { root, contract, cleanup: () => fs.rmSync(root, { recursive: true, force: true }) };
}

test("runtime validates exact version, commit, license and locked dependencies", () => {
  const f = fixture();
  try {
    const result = validateRuntime(f.root, f.contract, () => "1".repeat(40));
    assert.equal(result.version, "1.2.3");
    assert.equal(result.license, "TEST-ONLY");
    assert.equal(result.analyze_only, true);
  } finally { f.cleanup(); }
});

for (const field of ["commit", "version", "license", "license_sha256", "package_lock_sha256"]) {
  test(`runtime rejects changed ${field}`, () => {
    const f = fixture();
    try {
      const changed = { ...f.contract, [field]: field === "version" ? "9.9.9" :
        field === "license" ? "other" : "a".repeat(field === "commit" ? 40 : 64) };
      assert.throws(() => validateRuntime(f.root, changed, () => "1".repeat(40)), /binding_mismatch/);
    } finally { f.cleanup(); }
  });
}

test("missing runtime is unavailable rather than an available fallback", () => {
  const f = fixture();
  try {
    fs.rmSync(path.join(f.root, "index.js"));
    assert.throws(() => validateRuntime(f.root, f.contract, () => "1".repeat(40)), /runtime_unavailable/);
    assert.throws(() => validateRuntime(f.root, { ...f.contract, analyze_only: false }), /contract_invalid/);
  } finally { f.cleanup(); }
});
