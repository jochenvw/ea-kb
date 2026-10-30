# Enterprise Knowledge Base — formal ontology v0.1.0-draft.3

A deliberately small OWL 2 / RDF vocabulary, serialized in Turtle.

The ingestion skills use these semantic class names in typed labels and extraction contracts.
Runtime OWL loading, SHACL enforcement and reasoning are not integrated.
Run the optional commands below from the repository root.

## Files

- `ontology/ekb.ttl` — normative vocabulary: **seven classes**, including organizational scope, across four architecture layers; seven semantic relationships and four scope/classification properties.
- `ontology/ekb.shacl.ttl` — minimal validation of instance data.
- `examples/security-chain.ttl` — neutral examples of a four-layer lineage chain and nested organizational scopes; **not an assertion about any real company's policy**.

## Layers

1. **Company:** `StrategicGoal`, `StrategicRequirement`.
2. **Conceptual:** `ArchitectureDomain`, `ConceptualRequirement`.
3. **Logical:** `TechnicalRequirement`.
4. **Platform:** `PlatformRequirement`.

## Organizational scope

`OrganizationalScope` represents an organizational unit or boundary. `subScopeOf` points from
narrower to broader scope and is transitive. `appliesToScope` records the scope explicitly associated
with a goal or requirement. The ontology does not infer that applicability automatically propagates
to sub-scopes.

## Seven semantic relationships

`refines`, `supports`, `constrains`, `mapsTo`, `dependsOn`, `tensionWith`, `conflictsWith`.

`refines` always points from a **specific** requirement to a **broader** one. `mapsTo` never implies that the items are identical or that compliance is achieved. Tension and conflict are distinct claims, and are symmetric.

`inDomain`, `platformName`, and the two scope properties are metadata/structure, not additional
business-semantic relationship types.

## Intentionally separate from this ontology

This vocabulary is the application's ubiquitous language. `/ontology` is a read-only browser over
the canonical Turtle, also exposed at `/api/ontology`; definitions are not duplicated in UI code.
Domain/database/browser parity is enforced by `tests\unit\test_ontology.py`. See decision `0008`.

- Source documents and passage-level evidence
- Authority, publication maturity and governance approval
- Canonical identities, duplicate aliases, SemVer and supersession
- Reconciliation decisions and issue-management workflow
- Technical system facts and implementation verification

These are still mandatory parts of the wider knowledge system, but should be tracked in its provenance/governance schemas rather than conflated with the minimal **enterprise semantic ontology**.

Instances of platform-specific guidance are **not** automatically mandated enterprise policy. Scope and adoption are independent.

OWL uses open-world semantics. SHACL validates instances; OWL alone does not guarantee that an extracted claim is complete, applicable, or approved.

The `https://example.org/ekb/ontology#` namespace is a placeholder. Replace it with a stable organization-controlled IRI before publishing production data.

## Parse/validate Turtle

```powershell
uv run --with rdflib python -c 'from pathlib import Path; from rdflib import Graph; [print(p, len(Graph().parse(p, format="turtle")), "triples") for p in Path("ontology").rglob("*.ttl")]'
```

SHACL validation with pySHACL (optional):

```powershell
uv run --with pyshacl pyshacl -s ontology\ontology\ekb.shacl.ttl -e ontology\ontology\ekb.ttl ontology\examples\security-chain.ttl
```
