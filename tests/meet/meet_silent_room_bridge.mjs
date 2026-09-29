// Private test driver only. No grants, tasks, arbitrary JS or production policy.
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

export function command(line) {
  if (typeof line !== "string" || line.length > 128) throw new Error("silent_command_invalid");
  if (["inspect", "join-human", "stop"].includes(line)) return { action: line };
  if (/^bind room-[a-f0-9]{18}$/.test(line)) return { action: "bind", room: line.slice(5) };
  throw new Error("silent_command_invalid");
}

async function* lines(input) {
  let pending = "";
  for await (const chunk of input) {
    pending += chunk.toString("utf8");
    if (pending.length > 1024) throw new Error("silent_input_oversize");
    let end;
    while ((end = pending.indexOf("\n")) !== -1) {
      const line = pending.slice(0, end); pending = pending.slice(end + 1);
      yield command(line);
    }
  }
  if (pending) throw new Error("silent_input_incomplete");
}

async function main() {
  const cleanup = [], reply = value => process.stdout.write(JSON.stringify(value) + "\n");
  let stage = "setup";
  try {
    const helper = file => import(pathToFileURL(path.resolve(process.cwd(), "test/helpers", file)).href);
    const { machineBrowserFixture } = await helper("machine-browser-fixture.js");
    const { navigateFixture } = await helper("machine-browser-navigation.mjs");
    const f = await machineBrowserFixture({ after: close => cleanup.push(close) }, {
      tlsPortProxy: true, lifetimeSeconds: 240,
      hubPublicKey: await fs.readFile(process.env.MEET_TEST_HUB_PUBLIC_KEY, "utf8"),
      observeStage: value => { stage = value; },
    });
    f.human.setDefaultTimeout(8000);
    let room = null, humanMoved = false;
    const observe = async () => {
      const members = f.app.registry.members(room), machines = members.filter(p => p.machine);
      return { participants: members.length, machines: machines.length,
        machine_publications: machines.reduce((n, p) => n + p.publications.size, 0),
        fixed_ki_label: machines.every(p => p.name === "Ananta (KI)"),
        human_captures: await f.human.evaluate(() => window.__captures) };
    };
    reply({ origin: f.origin, certificate: f.certificatePath, test_network: f.testNetwork });
    stage = "commands";
    for await (const input of lines(process.stdin)) {
      if (input.action === "stop") break;
      if (input.action === "bind") {
        if (room !== null || input.room === f.roomId || f.app.registry.members(input.room).length !== 0) {
          throw new Error("silent_room_binding_invalid");
        }
        room = input.room;
        reply({ bound_empty_room: true });
        continue;
      }
      if (!room) throw new Error("silent_room_not_bound");
      if (input.action === "join-human") {
        if (humanMoved || f.app.registry.members(room).length !== 1
            || !f.app.registry.members(room)[0].machine) throw new Error("silent_first_machine_missing");
        if ((await observe()).human_captures !== 0) throw new Error("silent_unexpected_capture");
        await f.human.locator("#leave-room").click();
        await navigateFixture(f.human, f.origin + "/?room=" + room + "&mode=room",
          () => document.querySelector("#join-room")?.disabled === false);
        await f.human.locator("#join-room").click();
        await f.human.locator("#participant-count", { hasText: "2 / 20" }).waitFor();
        await f.human.locator(".nav-item").filter({ hasText: /^Live/ }).click();
        await f.human.locator("#mesh-analysis-navigation").click();
        await f.human.locator("app-machine-permissions-panel").getByRole("heading", { name: "Ananta (KI)", exact: true }).waitFor();
        humanMoved = true;
      }
      reply(await observe());
    }
  } catch (error) {
    reply({ bridge_error: "silent_room_gate_failed", stage,
      kind: error?.name === "TimeoutError" ? "TimeoutError" : "Error" });
    process.exitCode = 1;
  } finally {
    for (const close of cleanup.reverse()) {
      try { await close(); } catch { process.exitCode = 1; }
    }
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url
    && process.env.MEET_SILENT_ROOM_GATE === "1") await main();
