# SIRA adaptation license review

Reviewed 2026-08-27 against
`facebookresearch/sira@b33f5b71a1870f09b378d8e79afb6ad9e704709b`.

- The repository root declares the MIT license and Meta copyright.
- `src/sira/bm25x/NOTICE` declares derivation from LightOn bm25x under
  Apache-2.0 and identifies Meta modifications.
- No upstream source file, model weight, dataset or generated artifact was
  copied into Ananta.
- `worker/retrieval/sira/` is an independent implementation using standard
  Python and SQLite FTS5. It has no runtime dependency on `sira` or `bm25x`.
- The project name is used only to identify the research reference. It is not
  used as an Ananta product mark or performance guarantee.

Consequently, the current change adds attribution in `THIRD_PARTY_NOTICES` but
does not distribute upstream license-governed code. If a future change vendors,
patches or packages SIRA/bm25x, it must separately preserve the applicable MIT
and Apache-2.0 license text, copyrights, NOTICE content and prominent change
markings. Framework, model, dataset and produced-artifact licenses must remain
separate decisions.

The reviewed public reference has no direct Ananta SBOM component because it is
not installed or shipped. A dependency scanner must fail if a future lock or
image adds `sira`/`bm25x` without an approved component record and attribution.
This is an engineering review, not legal advice.
