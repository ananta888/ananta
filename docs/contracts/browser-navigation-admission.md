# Browser navigation target admission

Hub browser policy now shares a pure `BrowserNavigationTarget` parser across
navigation and download decisions. It has no network calls, storage or task
dispatch. Policy ownership stays in the Hub; workers still execute delegated
actions. Parsing, policy decisions and REST dispatch remain separate (SRP).

Only absolute HTTP(S) URLs up to 8192 characters are admitted. Credentials,
authority percent escapes, controls/space, backslashes, invalid/zero ports,
malformed bracket authorities and IPv6 scope identifiers fail closed. Paths
and queries may use normal percent encoding. ASCII DNS names (including ASCII
punycode), standard dotted IPv4 and bracketed IPv6 are supported. A single DNS
root dot and case are normalized for matching. Raw Unicode hostnames require
an explicit ASCII representation; no implicit IDNA transformation is guessed.

Browser URL parsing can interpret shortened, integer, octal and hexadecimal
IPv4 forms differently from Python's IP parser. This policy rejects these
noncanonical forms, including numeric final DNS labels, rather than treating
them as ordinary DNS names. See the [WHATWG URL host parsing rules](https://url.spec.whatwg.org/#host-parsing).

Non-global, multicast, reserved and IPv4-mapped private literals are denied,
as are IPv6 translation/tunnel literals. Localhost and the metadata hostname
remain denied even when a task supplies an empty/custom denylist. DNS patterns
match label boundaries; IP allowlists never match DNS suffixes. The download
whitelist retains exact-host semantics.

Camofox admission occurs before its navigation/download HTTP call. Audit events
retain session/action/reason metadata, not requested URLs, query secrets or
download paths. Browser-Use also applies the common restrictions at start and
rechecks explicit `navigate`, `open`, `goto` and `go_to_url` actions. These
actions require a URL field; a selector/target is not a URL authorization.

This is **not complete browser SSRF or screen-sharing protection**. DNS answers,
rebinding, redirects, subresources, click/form/script navigation and already
open pages still require enforcement at the actual browser network boundary.
A preflight DNS lookup alone would not provide that guarantee. Workspace and
page-epoch admission, privacy gates and the continuous Meet source remain open;
existing adapters still reject a live-view request. No production service or
network access policy was activated by these changes.

The existing Browser-Use execution method still combines trace construction,
action dispatch and policy sequencing (preserved SRP debt). This patch extracts
target parsing rather than introducing another parser into that method. A real
continuous browser adapter needs an injected request/egress enforcement port;
the synthetic action runner is not a security boundary or live capability.

Headless negatives in `tests/test_browser_navigation_target.py` prove rejection
before HTTP/action execution and content-free navigation audit events. They
use only deterministic in-memory targets; no private service is contacted.

The focused browser regression batch passed 177 tests in 114.05 seconds.
Four opt-in Camofox live checks and one opt-in Chromium probe were skipped in
that batch; the earlier private Chromium/GPU batch is recorded separately.
