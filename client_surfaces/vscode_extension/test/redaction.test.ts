import { describe, expect, it } from "vitest";
import { containsSecretKey, redactSensitiveText } from "../src/runtime/redaction";

describe("redaction", () => {
  it("detects secret-like keys", () => {
    expect(containsSecretKey("authorizationToken")).toBe(true);
    expect(containsSecretKey("api_key")).toBe(true);
    expect(containsSecretKey("profileId")).toBe(false);
  });

  it("redacts common secret payloads", () => {
    const raw = "Authorization: Bearer abcdefghijkl token=topsecret password=hunter2";
    const redacted = redactSensitiveText(raw);
    expect(redacted).not.toContain("abcdefghijkl");
    expect(redacted).not.toContain("topsecret");
    expect(redacted).not.toContain("hunter2");
    expect(redacted).toContain("***");
  });
});
