/** Finite starvation probe using real queue code, not a policy-change gate.
 * A fix must keep admission freshness and poll while execution is busy; it
 * must not make a first poll after 50 seconds accept stale input.
 */
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const source = process.argv[2];
if (!source || process.argv.length !== 3) {
  console.error('meet_chat_queue_source_required');
  process.exit(2);
}
const { MachineChatQueue } = await import(pathToFileURL(resolve(source)).href);
const scope = {
  origin: 'https://meet.invalid', tenant_id: 'synthetic-tenant', project_id: 'synthetic-project',
  task_id: 'synthetic-task', session_id: 'synthetic-session', runtime_id: 'synthetic-runtime',
  lease_id: 'synthetic-lease', generation: 1, room_id: 'room-aaaaaaaaaaaaaaaaaa',
  membership_epoch: 1, policy_revision: 1, own_peer_id: 'synthetic-machine', deadline_ms: 1_000_000,
};
function observed(delay) {
  let now = 1000;
  const queue = new MachineChatQueue({
    clock: () => now, authority: () => ({ scope, chatRead: true, chatSend: true }),
  });
  queue.push(JSON.stringify({
    schema: 'ananta.meet-chat-event.draft1', session_id: scope.session_id,
    generation: scope.generation, room_id: scope.room_id, membership_epoch: scope.membership_epoch,
    message_id: 'a'.repeat(32), sender_peer_id: 'synthetic-human', sender_kind: 'human',
    sent_at_ms: now, text: 'synthetic delegated input',
  }));
  now += delay;
  return queue.poll().events.length;
}
const timely = observed(500), afterBlockingTurn = observed(50_000);
const starvation = timely === 1 && afterBlockingTurn === 0;
console.log(JSON.stringify({
  schema: 'ananta.meet-chat-poll-starvation-probe.v1',
  classification: 'local_headless_synthetic_technical_observation',
  production_release_evidence: false, starvation_reproduced: starvation,
  timely_delivered: timely, delivered_after_50_seconds: afterBlockingTurn,
  reason_code: starvation ? 'meet_chat_poll_starved' : 'meet_chat_probe_inconclusive',
}));
process.exitCode = starvation ? 1 : 2;
