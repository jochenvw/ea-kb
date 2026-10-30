# Enterprise Knowledge Base — formal ontology v0.1.0-draft.1

A deliberately small OWL 2 / RDF vocabulary, serialized in Turtle.

Stored here as supplied; application loading, validation and reasoning are not yet integrated.
Run the optional commands below from the repository root.

## Files

- `ontology/ekb.ttl` — normative vocabulary: **six classes** across four layers, **seven semantic relationships** and two classification properties.
- `ontology/ekb.shacl.ttl` — minimal validation of instance data.
- `examples/security-chain.ttl` — synthetic example illustrating a four-layer lineage chain; **not an assertion about any real company's policy**.

## Layers

1. **Company:** `StrategicGoal`, `StrategicRequirement`.
2. **Conceptual:** `ArchitectureDomain`, `ConceptualRequirement`.
3. **Logical:** `TechnicalRequirement`.
4. **Platform:** `PlatformRequirement`.

## Seven semantic relationships

`refines`, `supports`, `constrains`, `mapsTo`, `dependsOn`, `tensionWith`, `conflictsWith`.

`refines` always points from a **specific** requirement to a **broader** one. `mapsTo` never implies that the items are identical or that compliance is achieved. Tension and conflict are distinct claims, and are symmetric.

`inDomain` and `platformName` are classification metadata, not additional business-semantic relationship types.

## Intentionally separate from this ontology

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
