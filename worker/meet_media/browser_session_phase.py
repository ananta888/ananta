"""Promise settlement transport only; callers own operation and authority budgets."""

START = """([operation, args, expectedUrl]) => {
  if (window.location.href !== expectedUrl) throw new Error('meet_machine_navigation_denied');
  const phase = { operation, status: 'pending' };
  window.__anantaPublicationPhase = phase;
  void Promise.resolve().then(() => window.anantaMachine[operation](...args)).then(
    () => { if (window.__anantaPublicationPhase === phase) phase.status = 'done'; },
    () => { if (window.__anantaPublicationPhase === phase) phase.status = 'failed'; });
}"""
STATE = "() => window.__anantaPublicationPhase?.status"
