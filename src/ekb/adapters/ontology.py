from importlib.resources import files
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from ekb.domain.models import (
    ClassificationProperty,
    RelationshipKind,
    SemanticType,
    content_hash,
)
from ekb.domain.ontology import OntologyCatalog, OntologyTerm


def ontology_bytes() -> bytes:
    resource = files("ekb").joinpath("ontology", "ekb.ttl")
    if resource.is_file():
        return resource.read_bytes()
    return (Path(__file__).parents[3] / "ontology" / "ontology" / "ekb.ttl").read_bytes()


def load_ontology() -> OntologyCatalog:
    raw = ontology_bytes()
    graph = Graph().parse(data=raw.decode("utf-8"), format="turtle")
    ontologies = list(graph.subjects(RDF.type, OWL.Ontology))
    if len(ontologies) != 1:
        raise ValueError("Expected one authoritative OWL ontology")
    ontology = ontologies[0]
    namespace = str(ontology) + "#"

    def literal(subject: URIRef, predicate: URIRef) -> str:
        values = [
            str(value)
            for value in graph.objects(subject, predicate)
            if isinstance(value, Literal) and value.language in {None, "en"}
        ]
        if len(values) != 1:
            raise ValueError(f"Expected one English definition/label for {subject}")
        return values[0]

    terms = []
    for owl_type, kind in (
        (OWL.Class, "class"),
        (OWL.ObjectProperty, "object_property"),
        (OWL.DatatypeProperty, "datatype_property"),
    ):
        for subject in sorted(graph.subjects(RDF.type, owl_type), key=str):
            iri = str(subject)
            if not isinstance(subject, URIRef) or not iri.startswith(namespace):
                raise ValueError("Vocabulary terms must use the ontology namespace")
            name = iri.removeprefix(namespace)
            terms.append(
                OntologyTerm.model_validate(
                    {
                        "name": name,
                        "iri": iri,
                        "label": literal(subject, RDFS.label),
                        "definition": literal(subject, RDFS.comment),
                        "kind": kind,
                        "category": "concept"
                        if kind == "class"
                        else "relation"
                        if name in set(RelationshipKind)
                        else "classification",
                        "domains": sorted(str(v) for v in graph.objects(subject, RDFS.domain)),
                        "ranges": sorted(str(v) for v in graph.objects(subject, RDFS.range)),
                        "characteristics": [
                            label
                            for rdf_type, label in (
                                (OWL.SymmetricProperty, "symmetric"),
                                (OWL.TransitiveProperty, "transitive"),
                            )
                            if (subject, RDF.type, rdf_type) in graph
                        ],
                    }
                )
            )
    classes = {term.name for term in terms if term.kind == "class"}
    properties = {term.name for term in terms if term.kind != "class"}
    if classes != set(SemanticType) or properties != (
        set(RelationshipKind) | set(ClassificationProperty)
    ):
        raise ValueError("Domain vocabulary has drifted from the authoritative OWL ontology")
    return OntologyCatalog(
        iri=str(ontology),
        label=literal(URIRef(str(ontology)), RDFS.label),
        definition=literal(URIRef(str(ontology)), RDFS.comment),
        version=literal(URIRef(str(ontology)), OWL.versionInfo),
        namespace=namespace,
        sha256=content_hash(raw),
        terms=tuple(terms),
    )
