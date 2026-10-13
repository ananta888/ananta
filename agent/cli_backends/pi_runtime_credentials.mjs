/** Read-only credentials for exactly the Hub-selected Pi provider. */
export function selectedPiCredentials(key) {
  const deny = () => { throw new Error('pi_credential_operation_not_authorized'); };
  if (typeof key !== 'string' || !key || key.length > 8192 || /[\x00-\x1f]/.test(key)) deny();
  return Object.freeze({
    async read(provider, options = {}) {
      options.signal?.throwIfAborted();
      if (provider !== 'ananta') deny();
      return { type: 'api_key', key };
    },
    async list(options = {}) {
      options.signal?.throwIfAborted();
      return [{ providerId: 'ananta', type: 'api_key' }];
    },
    async modify() { deny(); },
    async delete() { deny(); },
  });
}
