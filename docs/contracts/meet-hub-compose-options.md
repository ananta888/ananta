# Hub Compose policy forwarding (MAP-32)

Source audit at `83e806663`: the existing Hub overlay did not expose the
implemented key ID, room allocation, Organization principals, task/room
preauthorization, reconnect, media timing, speaker policy, dialog capacity or
optional publisher endpoints. Setting those variables in a deployment's
interpolation environment alone therefore did not configure its Hub container.

The overlay now forwards those twelve settings. New feature defaults remain
disabled; policy arrays are empty and the capacity object retains the existing
bounded default profile. Explicit empty/invalid strict values reach the existing
Hub parser rather than being silently replaced. `ANANTA_MEET_DIALOG_WORKER_URLS`
uses nullable Compose forwarding: absence preserves the old selector-free
composition, while explicit `[]`, empty or malformed strings are not rewritten.
The existing bootstrap remains the sole validator and policy owner (SRP/DIP).
There is no alternate parser or Worker-selected routing authority.

Compose merge precedence still applies: environment entries in this later
overlay override same-named entries from an earlier base file or service
`env_file`. Operators with custom earlier-file definitions must supply their
intended values through the explicit interpolation environment/`--env-file` and
review the merged deployment before applying it. This source change does not
edit any operator file or update/restart an existing container.

Seven new real-Compose checks first failed against the old template in
11.62 seconds, each due to a missing forwarded field. After the correction,
all **16** new forwarding and existing device/egress-composition checks passed
in **18.59 seconds**. They cover defaults, exact supplied values, optional
unset/empty/array/malformed publisher values, explicit test dotenv input,
invalid feature/policy strings and unchanged network/secret/device/egress
boundaries. Rendering uses only a synthetic base document, an explicit empty
or synthetic env file, a closed subprocess timeout and no operator environment.
No container, network, runtime directory or secret is provisioned.

Ruff and whitespace checks pass. These results verify Compose rendering, not
live Hub startup, task approval, multi-Worker operation or a public deployment.
Those implementations retain their separate bootstrap and native acceptance
tests. No runtime/Worker-image code was changed by this template correction.
