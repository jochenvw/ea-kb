from typing import Literal

from ekb.domain.models import Model


class OntologyTerm(Model):
    name: str
    iri: str
    label: str
    definition: str
    kind: Literal["class", "object_property", "datatype_property"]
    category: Literal["concept", "relation", "classification"]
    domains: tuple[str, ...] = ()
    ranges: tuple[str, ...] = ()
    characteristics: tuple[Literal["symmetric", "transitive"], ...] = ()


class OntologyCatalog(Model):
    iri: str
    label: str
    definition: str
    version: str
    namespace: str
    sha256: str
    terms: tuple[OntologyTerm, ...]
