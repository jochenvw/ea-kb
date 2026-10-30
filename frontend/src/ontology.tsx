import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { Badge } from "@/components/ui/badge";
import {
  Empty,
  Heading,
  LoadState,
  Panel,
  SearchBox,
} from "@/components/workspace";
import { useResource } from "@/lib/api";
import type { OntologyCatalog, OntologyTerm } from "@/types";

type OntologyResource = ReturnType<typeof useResource<OntologyCatalog>>;
const OntologyContext = createContext<OntologyResource | null>(null);

function useOntology() {
  const resource = useContext(OntologyContext);
  if (!resource) throw new Error("OntologyProvider is required");
  return resource;
}

export function OntologyProvider({ children }: { children: ReactNode }) {
  const resource = useResource<OntologyCatalog>("/api/ontology");
  return (
    <OntologyContext.Provider value={resource}>
      {children}
    </OntologyContext.Provider>
  );
}

export function OntologyBadge({ name }: { name: string | null | undefined }) {
  const catalog = useOntology().data;
  const term = catalog?.terms.find((item) => item.name === name);
  if (!name) return <Badge variant="secondary">Unclassified</Badge>;
  return (
    <a href={`/ontology#${encodeURIComponent(name)}`} title={term?.definition}>
      <Badge variant="secondary">{term?.label ?? name}</Badge>
    </a>
  );
}

export function Ontology() {
  const resource = useOntology();
  const [query, setQuery] = useState("");
  const catalog = resource.data;
  useEffect(() => {
    const target = catalog?.terms.find(
      (term) => `#${encodeURIComponent(term.name)}` === window.location.hash,
    );
    if (target) document.getElementById(target.name)?.scrollIntoView();
  }, [catalog]);
  const terms = (catalog?.terms ?? []).filter((term) =>
    [term.name, term.label, term.definition, term.iri]
      .join(" ")
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const sections = [
    [
      "concept",
      "Concepts",
      "OWL classes define the application's shared concepts.",
    ],
    [
      "relation",
      "Semantic relations",
      "Directed subject-to-object links. Symmetric relations work in both directions.",
    ],
    [
      "classification",
      "Classification and scope",
      "Applicability, architecture domains and platform names are distinct from semantic links.",
    ],
  ];
  return (
    <>
      <Heading
        eyebrow="SHARED DOMAIN LANGUAGE"
        title="Ontology"
        description="Read-only concepts, relations and definitions from the authoritative OWL/RDF vocabulary."
      >
        <Badge variant="outline">Read-only</Badge>
      </Heading>
      <LoadState resource={resource}>
        {catalog && (
          <>
            <Panel title={catalog.label} description={catalog.definition}>
              <p>
                <strong>Version {catalog.version}</strong> ·{" "}
                {catalog.terms.length} terms
              </p>
              <p>
                <code>{catalog.iri}</code>
              </p>
              <p className="table-note">
                Requirement classes are peers, not subclasses. Scope containment
                does not automatically inherit applicability. Classification
                does not establish enterprise approval. No OWL reasoning is
                performed.
              </p>
              <details>
                <summary>Source identity</summary>
                <p>
                  Namespace: <code>{catalog.namespace}</code>
                </p>
                <p>
                  SHA-256: <code>{catalog.sha256}</code>
                </p>
              </details>
            </Panel>
            <SearchBox
              name="Search ontology"
              placeholder="Search concepts, relations or definitions"
              value={query}
              onChange={setQuery}
            />
            {terms.length === 0 && (
              <Empty
                title="No matching ontology terms"
                description="Try another name or definition."
              />
            )}
            {sections.map(([category, title, description]) => {
              const matches = terms.filter(
                (term) => term.category === category,
              );
              return matches.length ? (
                <Panel key={category} title={title} description={description}>
                  <div className="ontology-grid">
                    {matches.map((term) => (
                      <Term key={term.iri} term={term} catalog={catalog} />
                    ))}
                  </div>
                </Panel>
              ) : null;
            })}
          </>
        )}
      </LoadState>
    </>
  );
}

function Term({
  term,
  catalog,
}: {
  term: OntologyTerm;
  catalog: OntologyCatalog;
}) {
  function reference(iri: string) {
    const target = catalog.terms.find((item) => item.iri === iri);
    return target ? (
      <a href={`/ontology#${target.name}`}>{target.label}</a>
    ) : (
      <code>{iri}</code>
    );
  }
  return (
    <article id={term.name} className="ontology-term">
      <h3>
        <a href={`#${term.name}`}>{term.label}</a>
      </h3>
      <code>ekb:{term.name}</code>
      <p>{term.definition}</p>
      <div className="ontology-characteristics">
        <Badge variant="outline">
          {term.kind === "class"
            ? "OWL class"
            : term.kind === "object_property"
              ? "Object property"
              : "Datatype property"}
        </Badge>
        {term.characteristics.map((value) => (
          <Badge key={value} variant="outline">
            {value}
          </Badge>
        ))}
      </div>
      {(term.domains.length > 0 || term.ranges.length > 0) && (
        <dl>
          {term.domains.map((iri) => (
            <div key={`domain-${iri}`}>
              <dt>Domain</dt>
              <dd>{reference(iri)}</dd>
            </div>
          ))}
          {term.ranges.map((iri) => (
            <div key={`range-${iri}`}>
              <dt>Range</dt>
              <dd>{reference(iri)}</dd>
            </div>
          ))}
        </dl>
      )}
      <details>
        <summary>Full IRI</summary>
        <code>{term.iri}</code>
      </details>
    </article>
  );
}
