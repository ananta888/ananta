// Real endpoint/queue execution against synthetic clock, identity and transport.
// This fixture has no browser, provider, deployment or release-evidence authority.
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";

const { MachineChatEndpoint } = await import(pathToFileURL(process.env.ANANTA_TEST_MEET_CHAT_ENDPOINT_BUNDLE));
const epoch = 1_788_000_000_000;
let now = epoch, listener = null;
const room = "room-aaaaaaaaaaaaaaaaaa";
let authority = { chatRead: true, chatSend: true, scope: {
  origin: "https://meet.invalid", tenant_id: "tenant", project_id: "project", task_id: "task",
  session_id: "session", runtime_id: "runtime", lease_id: "lease", room_id: room,
  own_peer_id: "2222222222222222", generation: 1, membership_epoch: 1, policy_revision: 1, deadline_ms: epoch + 60_000,
} };
const admitted = new Set();
const endpoint = new MachineChatEndpoint({ authority: () => authority, clock: () => now,
  sourceAllowed: () => true, subscribe: callback => { listener = callback; return () => { listener = null; }; },
  sendReply: (_text, _id) => ({ messageId: "f".repeat(32), queuedPeers: 1 }) });
const input = createInterface({ input: process.stdin, terminal: false });
input.on("line", line => {
  try {
    const command = JSON.parse(line); now = epoch + Math.round(command.now * 1000);
    let result;
    if (command.operation === "status") result = { joined: true, peers: 1, e2ee: true, chat: [] };
    else if (command.operation === "renew") {
      authority = { ...authority, scope: { ...authority.scope, generation: authority.scope.generation + 1,
        deadline_ms: now + 60_000 } }; result = null;
    } else if (command.operation === "poll") {
      for (let number = 1; number <= command.message_count; number++) {
        const sentAt = epoch + (number === 1 ? 0 : 10_000);
        if (listener && now >= sentAt && now - sentAt <= 30_000 && !admitted.has(number)) {
          admitted.add(number);
          listener({ version: 2, type: "chat", roomId: room, membershipEpoch: 1,
            messageId: number.toString(16).padStart(32, "0"), replyTo: "", sentAt,
            text: `synthetic question ${number}`, senderPeerId: "1111111111111111", senderKind: "human" });
        }
      }
      result = endpoint.poll(command.argument);
    } else if (command.operation === "open") result = endpoint.open();
    else if (command.operation === "redeliver") result = endpoint.redeliver(command.argument);
    else if (command.operation === "reply") result = endpoint.reply(...command.argument);
    else if (command.operation === "ack") { endpoint.ack(command.argument); result = null; }
    else throw new Error("synthetic_bridge_operation_invalid");
    process.stdout.write(JSON.stringify({ result }) + "\n");
  } catch (error) {
    process.stdout.write(JSON.stringify({ error: /^meet_chat_[a-z_]+$/.test(error.message)
      ? error.message : "synthetic_bridge_failed" }) + "\n");
  }
});
input.on("close", () => { endpoint.close(); });
