# Scientific Skills Pilot

The Scientific Skills pilot projects five reviewed K-Dense skill documents into
Ananta's existing Hub-controlled context flow. It does not install or import an
upstream package, execute upstream scripts, or grant network and file-write
capabilities.

## Admitted pilot set

All entries are bound to upstream commit
`cc37669ed0f354619b1ae586e958609a87680718` and are restricted to
`documentation-only` regardless of the broader capabilities described by the
upstream project:

- `astropy`
- `networkx`
- `scvi-tools`
- `torch-geometric`
- `umap-learn`

The immutable entry IDs, content hashes, risk-profile digests, context budgets,
and approval receipt digests are stored in
`config/scientific-skills-catalog.json`. The reproducible review inputs are in
`config/scientific-skills-pilot-review.json`.

## Visibility and selection

The committed catalog is disabled by default. A caller can see the pilot only
when all of these conditions hold:

1. the selected catalog revision has `feature_enabled: true`;
2. the authenticated principal has the `scientific_skill_pilot` role (or is an
   administrator);
3. the principal passes the Hub's canonical tenant/project source-access policy.

The Hub returns a bounded card before selection. It includes the exact pin,
allowed mode, context budget, network profile, approval status, and pinned source
link. Missing flags, missing grants, and foreign project scopes return no cards.
Every selection or rejection must reach the injected audit port; an audit outage
fails closed.

The Angular presentation component is
`ScientificSkillPilotCatalogComponent`. It renders nothing unless both the
feature flag and permission are true and exposes the provenance and capability
fields before a user selects a skill.

## Security boundary

Pilot selection delegates only to `DocumentationResearchSkillAdapter`. Its
projection explicitly forbids executing upstream files, installing dependencies,
or expanding capabilities. The five entries have no allowed tools, use the
`denied` network profile, require no approval-time credentials, and cannot submit
worker execution requests.

Upstream changes are never adopted from a moving branch. A new pin must first
pass the manifest, risk, catalog-binding, compatibility-diff, and negative policy
gates and then be published as a new immutable catalog revision.
