import { describe, expect, it } from "vitest";
import { buildWebFallbackUrl } from "../src/runtime/webFallback";

describe("buildWebFallbackUrl", () => {
  it("builds safe fallback URLs from configured base URL", () => {
    expect(buildWebFallbackUrl("http://localhost:8080/api", "tasks", "task-1")).toBe("http://localhost:8080/tasks/task-1");
    expect(buildWebFallbackUrl("http://localhost:8080", "config")).toBe("http://localhost:8080/config");
    expect(buildWebFallbackUrl("http://localhost:8080", "audit", "audit-1", "trace-1")).toBe(
      "http://localhost:8080/audit/audit-1?trace=trace-1"
    );
  });

  it("rejects unsupported base URL protocols", () => {
    expect(buildWebFallbackUrl("javascript:alert(1)", "tasks", "x")).toBeNull();
  });
});
