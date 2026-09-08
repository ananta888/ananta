"""Read one bounded closed local observation; do not await a browser Promise."""

from ananta_contracts.meet_client_probe import require_client_probe

READ_PROBE = """() => {
  const api = window.anantaMachine;
  if (!api || !('probe' in api)) return {present: false};
  try {
    if (typeof api.probe !== 'function') return {present: true, value: null};
    const value = api.probe();
    if (value && typeof value.then === 'function') return {present: true, value: null};
    return {present: true, value};
  } catch { return {present: true, value: null}; }
}"""


def check_client_probe(page, checkpoint, capabilities, *, mp4=False):
    checkpoint()
    result = page.evaluate(READ_PROBE)
    checkpoint()
    if type(result) is dict and result == {"present": False} and result["present"] is False:
        return False  # Legacy fixed browser, not a successful feasibility probe.
    if type(result) is not dict or set(result) != {"present", "value"} or result["present"] is not True:
        raise ValueError("meet_client_probe_invalid")
    require_client_probe(result["value"], capabilities, mp4=mp4)
    return True
