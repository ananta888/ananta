# Image and spoken replies across three real lease renewals

## Source check (MAP-11/20/29/30)

The existing `avatar-image-renewal` scenario verifies one actual lease change.
The earlier five-minute text/screen soak observed multiple generations but its
speech branch pauses speech before the long observation. Neither is evidence
of successful new spoken replies and image hydration through three renewals.

Add a separate opt-in `avatar-image-renewal-series` case, selected only with
`MEET_DIALOG_SOAK_SECONDS=360`. Keep real clocks, the production 120-second
grants and normal Hub renewal. Use a 360-second parent and the existing isolated
fixture lifetime; no accelerated expiry, substitute lease or policy retry.

After red/blue replacement and the first complete spoken reply, require actual
generations 2, 3 and 4 in order. Each must have matching image hydration, a
decoded blue remote avatar, moving screen, no old speech replay, and a newly
correlated spoken reply completed locally plus non-silent remote audio. Then
revoke the image and verify that another spoken reply still works before final
parent stop. Retain the existing single-renewal scenario unchanged.

Keep the observer passive and bounded; extend its exact-generation wait instead
of embedding renewal policy in a test Worker. No generated identities, grant
replays, human capture, production credentials or unrelated process changes.
Tests classify image, policy and tone as synthetic. This is a private single-host
multi-renewal check, not a six-minute active soak, two-hour soak, real GPU run,
multi-agent/multi-host acceptance or production release evidence.

Exercise the generation selection and scenario classification with deterministic
fixtures first, then run the one extended browser case serially. Preserve any
failed attempts and fix their actual cause without extending safety budgets.
