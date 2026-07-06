import fs from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const AIDetector = require("/workspace/patterns.js");
const request = JSON.parse(fs.readFileSync("/workspace/input.json", "utf8"));
const result = AIDetector.analyzeText(String(request.text || ""), request.options || {});
process.stdout.write(JSON.stringify(result));
