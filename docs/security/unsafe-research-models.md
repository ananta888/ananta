# Unsafe research models

`unsafe_research` means the model was intentionally safety-modified and is not
a normal candidate. It is default-off and permanently excluded from automatic
routing and production. A bounded Hub authorization names purpose, exact model
revision, runtime and expiry; expiry fails closed without interaction.

Research execution uses the no-network, no-tools, read-only sandbox in
`config/security/unsafe-research-sandbox.v1.json`. Removing the profile or its
authorization disables future starts; immutable artifacts and audit records
remain until the normal retention process removes them.
