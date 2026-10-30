import re
from pathlib import Path

import httpx
import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from ekb.adapters.inference import LABEL_INSTRUCTIONS, SCOPED_INSTRUCTIONS
from ekb.adapters.ontology import load_ontology, ontology_bytes
from ekb.adapters.persistence.postgres.models import ConceptRow, RelationshipRow, VersionRow
from ekb.domain.models import ClassificationProperty, RelationshipKind, SemanticType
from ekb.interfaces.api import create_app


def test_catalog_preserves_authoritative_definitions_and_owl_semantics():
    catalog = load_ontology()
    graph = Graph().parse(data=ontology_bytes().decode(), format="turtle")
    for term in catalog.terms:
        assert term.definition == str(graph.value(URIRef(term.iri), RDFS.comment))
        assert term.label == str(graph.value(URIRef(term.iri), RDFS.label))
        if term.kind == "class":
            assert term.definition in LABEL_INSTRUCTIONS
        assert term.definition in SCOPED_INSTRUCTIONS
    terms = {term.name: term for term in catalog.terms}
    assert terms["subScopeOf"].characteristics == ("transitive",)
    assert terms["subScopeOf"].ranges == (catalog.namespace + "OrganizationalScope",)
    assert terms["tensionWith"].characteristics == ("symmetric",)
    assert terms["conflictsWith"].characteristics == ("symmetric",)
    assert terms["platformName"].kind == "datatype_property"
    assert not list(graph.triples((None, RDFS.subClassOf, None)))
    assert set(graph.subjects(RDF.type, OWL.Class)) == {
        URIRef(term.iri) for term in catalog.terms if term.kind == "class"
    }


def test_ontology_language_agrees_across_domain_database_and_browser():
    catalog = load_ontology()
    classes = {term.name for term in catalog.terms if term.kind == "class"}
    relations = {term.name for term in catalog.terms if term.category == "relation"}
    classification = {term.name for term in catalog.terms if term.category == "classification"}
    assert classes == set(SemanticType)
    assert relations == set(RelationshipKind)
    assert classification == set(ClassificationProperty)

    def values(table, name):
        constraint = next(item for item in table.constraints if item.name == name)
        return set(re.findall(r"'([^']+)'", str(constraint.sqltext)))

    assert values(VersionRow.__table__, "version_semantic_type") == classes
    assert values(RelationshipRow.__table__, "ontology_relationship_kind") == relations
    browser = (Path("frontend") / "src" / "types.ts").read_text(encoding="utf-8")
    union = browser.split("export type SemanticType =", 1)[1].split(";", 1)[0]
    assert set(re.findall(r'"([^"]+)"', union)) == classes
    assert "kind" not in ConceptRow.__table__.columns


@pytest.mark.asyncio
async def test_ontology_is_read_only_without_a_database_and_uses_local_access_controls():
    app = create_app()
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        response = await client.get("/api/ontology")
        assert response.status_code == 200
        assert response.json() == load_ontology().model_dump(mode="json")
        assert (await client.get("/ontology")).status_code == 200
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert (await client.request(method, "/api/ontology", json={})).status_code == 405
        assert (
            await client.get("/api/ontology", headers={"x-forwarded-for": "203.0.113.7"})
        ).status_code == 403
    remote = httpx.ASGITransport(app=app, client=("203.0.113.7", 41000))
    async with httpx.AsyncClient(transport=remote, base_url="http://localhost") as client:
        assert (await client.get("/api/ontology")).status_code == 403
