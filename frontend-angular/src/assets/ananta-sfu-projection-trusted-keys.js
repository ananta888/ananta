/*
 * Fail-closed runtime bootstrap for SFU projection verification.
 *
 * Deployments with SFU projection signing enabled replace this public asset
 * with the Hub-provided keyset. Keeping an explicit empty bootstrap in the
 * application image avoids a missing-script error without trusting any key.
 */
globalThis.__ANANTA_SFU_PROJECTION_TRUSTED_KEYS__ = Object.freeze({
  schema: 'ananta.sfu-projection-trusted-keyset.v1',
  keysetVersion: 0,
  keys: Object.freeze([]),
});
