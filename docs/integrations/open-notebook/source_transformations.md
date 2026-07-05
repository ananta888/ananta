# OpenNotebook source transformations

Transformations are synchronous, hub-governed operations over an existing
`SourceReference`. They reuse source-focused retrieval and never read raw
OpenNotebook objects directly. Outputs are stored as derived artifacts with
`record_kind=source_insight`, their parent reference, transformation ID and
output hash.

The built-in templates cover summary, key terms, architecture facts, API
contracts, security notes, testable claims and citation candidates. Template
IDs and ordering are deterministic.
