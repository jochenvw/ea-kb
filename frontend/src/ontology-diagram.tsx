import { useEffect, useId, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import type { OntologyCatalog, OntologyTerm } from "@/types";

function mermaidText(value: string) {
  return value
    .replaceAll("#", "#35;")
    .replaceAll("&", "#38;")
    .replaceAll('"', "#quot;")
    .replaceAll("<", "#60;")
    .replaceAll(">", "#62;")
    .replace(/[\u0000-\u001f\u007f]/g, " ");
}

function diagramSource(catalog: OntologyCatalog) {
  const ids = new Map(
    catalog.terms.map((term, index) => [term.iri, `t${index}`]),
  );
  const classes = catalog.terms.filter((term) => term.kind === "class");
  const relations = catalog.terms.filter(
    (term) => term.category === "relation",
  );
  const external = [
    ...new Set(
      catalog.terms.flatMap((term) => [...term.domains, ...term.ranges]),
    ),
  ].filter((iri) => !ids.has(iri));
  external.forEach((iri, index) => ids.set(iri, `x${index}`));

  function group(title: string, terms: OntologyTerm[], classTerms = false) {
    if (!terms.length) return [];
    return [
      `  subgraph ${title.replaceAll(" ", "_")}["${title}"]`,
      ...terms.map(
        (term) =>
          `    ${ids.get(term.iri)}["${mermaidText(
            `${classTerms ? "«owl:Class» " : ""}${term.label}${term.characteristics.length ? ` (${term.characteristics.join(", ")})` : ""}`,
          )}"]`,
      ),
      "  end",
    ];
  }

  const lines = [
    "flowchart TB",
    ...group(
      "Company intent",
      classes.filter((term) =>
        ["StrategicGoal", "StrategicRequirement"].includes(term.name),
      ),
      true,
    ),
    ...group(
      "Platform-agnostic architecture",
      classes.filter((term) =>
        [
          "ArchitectureDomain",
          "ConceptualRequirement",
          "TechnicalRequirement",
        ].includes(term.name),
      ),
      true,
    ),
    ...group(
      "Platform-specific implementation",
      classes.filter((term) => term.name === "PlatformRequirement"),
      true,
    ),
    ...group(
      "Organizational applicability",
      classes.filter((term) => term.name === "OrganizationalScope"),
      true,
    ),
    ...group("Semantic relationships", relations),
    ...group(
      "Classification and scope",
      catalog.terms.filter((term) => term.category === "classification"),
    ),
    ...(external.length
      ? [
          '  subgraph External_types["External types"]',
          ...external.map((iri) => {
            const label = iri.startsWith("http://www.w3.org/2001/XMLSchema#")
              ? `xsd:${iri.split("#").at(-1)}`
              : iri;
            return `    ${ids.get(iri)}["${mermaidText(label)}"]`;
          }),
          "  end",
        ]
      : []),
    ...catalog.terms.flatMap((term) =>
      term.domains.length && term.ranges.length
        ? term.domains.flatMap((domain) =>
            term.ranges.map(
              (range) =>
                `  ${ids.get(domain)} -->|"${mermaidText(term.label)}"| ${ids.get(range)}`,
            ),
          )
        : [
            ...term.domains.map(
              (iri) => `  ${ids.get(term.iri)} -->|domain| ${ids.get(iri)}`,
            ),
            ...term.ranges.map(
              (iri) => `  ${ids.get(term.iri)} -->|range| ${ids.get(iri)}`,
            ),
          ],
    ),
    ...(classes.length && relations.length
      ? [
          "  %% Invisible positioning only; this is not an ontology statement.",
          `  ${ids.get(classes[0].iri)} ~~~ ${ids.get(relations[0].iri)}`,
        ]
      : []),
  ];
  return lines.join("\n");
}

export function OntologyDiagram({ catalog }: { catalog: OntologyCatalog }) {
  const source = useMemo(() => diagramSource(catalog), [catalog]);
  const id = `ontology-${useId().replaceAll(":", "")}`;
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{
    svg: string | null;
    error: string | null;
  }>({
    svg: null,
    error: null,
  });

  useEffect(() => {
    let active = true;
    setResult({ svg: null, error: null });
    async function render() {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          suppressErrorRendering: true,
          htmlLabels: true,
          layout: "dagre",
          fontFamily: "Segoe UI, Aptos, Calibri, sans-serif",
          flowchart: { useMaxWidth: false },
        });
        const { svg } = await mermaid.render(`${id}-${attempt}`, source);
        if (active) setResult({ svg, error: null });
      } catch (error: unknown) {
        if (active) {
          setResult({
            svg: null,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      }
    }
    void render();
    return () => {
      active = false;
    };
  }, [source, id, attempt]);

  return (
    <>
      <p className="table-note">
        Class boxes are the OWL concepts grouped by their architecture layer.
        Labeled arrows show only declared domain/range constraints. Relationship
        properties without those constraints remain separate rather than
        inventing class links. The complete definitions remain in the catalog.
      </p>
      {result.error ? (
        <div className="empty" role="alert">
          <strong>Could not render the Mermaid diagram</strong>
          <p>{result.error}</p>
          <Button
            variant="outline"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Try again
          </Button>
        </div>
      ) : result.svg ? (
        <figure
          className="ontology-diagram"
          aria-label="OWL classes, properties and declared ontology constraints"
          dangerouslySetInnerHTML={{ __html: result.svg }}
        />
      ) : (
        <p role="status">Rendering Mermaid diagram…</p>
      )}
      <details className="technical">
        <summary>View Mermaid source</summary>
        <pre className="ontology-mermaid-source">{source}</pre>
      </details>
    </>
  );
}
