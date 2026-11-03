import { useEffect, useState } from "react";
import { Printer } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Filter, Heading, LoadState } from "@/components/workspace";
import { assertionText, label, levelLabel, useResource } from "@/lib/api";
import type { Concept, Source } from "@/types";

// The concepts API returns at most 200 items per request; the export states when more exist.
const LIMIT = 200;
const LAYERS = ["company", "platform_agnostic", "platform_specific"] as const;
type Layer = (typeof LAYERS)[number];

function query(layer: Layer, sourceId: string, limit: number, after?: string) {
  const params = new URLSearchParams({ limit: String(limit), level: layer });
  if (sourceId) params.set("source_id", sourceId);
  if (after) params.set("after", after);
  return `/api/concepts?${params}`;
}

function selected(concept: Concept) {
  return (
    concept.versions.find((item) => item.id === concept.matching_version_id) ??
    concept.versions.at(-1)
  );
}

function canonical(concept: Concept) {
  const version = selected(concept);
  return (concept.authority_states ?? []).some(
    (state) =>
      state.concept_version_id === version?.id &&
      state.canonical_status === "canonical",
  );
}

export function RulesExport() {
  const params = new URLSearchParams(location.search);
  const [layer, setLayer] = useState(params.get("layer") ?? "");
  const [sourceId, setSourceId] = useState(params.get("source_id") ?? "");
  const [status, setStatus] = useState(params.get("status") ?? "");
  const [generated] = useState(() => new Date().toISOString());
  const [statements, setStatements] = useState<Record<string, string>>({});
  const sources = useResource<Source[]>("/api/sources");
  const sourceName =
    sources.data?.find((source) => source.item.id === sourceId)?.item.name ??
    sourceId;

  function update(key: string, value: string, set: (value: string) => void) {
    set(value);
    const next = new URLSearchParams(location.search);
    if (value) next.set(key, value);
    else next.delete(key);
    history.replaceState(null, "", `${location.pathname}?${next}`);
  }

  return (
    <article className="rules-export">
      <Heading
        eyebrow="REFERENCE / RULES EXPORT"
        title="Extracted rules for visual verification."
        description="Company intent, then platform-agnostic architecture, then platform controls, each with its exact source excerpt. Use the browser's Print to PDF."
      >
        <Button variant="outline" onClick={() => window.print()}>
          <Printer />
          Print or save as PDF
        </Button>
      </Heading>
      <div className="toolbar no-print">
        <Filter
          name="Layer"
          value={layer}
          onChange={(value) => update("layer", value, setLayer)}
          options={[
            ["", "All layers"],
            ...LAYERS.map((item): [string, string] => [item, levelLabel(item)]),
          ]}
        />
        <Filter
          name="Source"
          value={sourceId}
          onChange={(value) => update("source_id", value, setSourceId)}
          options={[
            ["", "All sources"],
            ...(sources.data ?? [])
              .slice()
              .sort((a, b) => a.item.name.localeCompare(b.item.name))
              .map((source): [string, string] => [
                source.item.id,
                source.item.name,
              ]),
          ]}
        />
        <Filter
          name="Canonical status"
          value={status}
          onChange={(value) => update("status", value, setStatus)}
          options={[
            ["", "Canonical and candidates"],
            ["canonical", "Canonical only"],
            ["candidate", "Candidates only"],
          ]}
        />
      </div>
      <dl className="export-meta">
        <dt>Generated</dt>
        <dd>{generated}</dd>
        <dt>Layer</dt>
        <dd>{layer ? levelLabel(layer) : "All layers"}</dd>
        <dt>Source</dt>
        <dd>{sourceId ? sourceName : "All sources"}</dd>
        <dt>Canonical status</dt>
        <dd>
          {status === "canonical"
            ? "Canonical only"
            : status === "candidate"
              ? "Candidates only"
              : "Canonical and candidates"}
        </dd>
      </dl>
      <p className="table-note">
        Candidates are extracted interpretations awaiting human approval; they
        are not adopted policy. Each layer lists at most {LIMIT} items and says
        so when more exist.
      </p>
      {LAYERS.filter((item) => !layer || item === layer).map((item) => (
        <LayerSection
          key={item}
          layer={item}
          sourceId={sourceId}
          status={status}
          statements={statements}
          onLoaded={(loaded) =>
            setStatements((current) => ({ ...current, ...loaded }))
          }
        />
      ))}
    </article>
  );
}

function LayerSection({
  layer,
  sourceId,
  status,
  statements,
  onLoaded,
}: {
  layer: Layer;
  sourceId: string;
  status: string;
  statements: Record<string, string>;
  onLoaded: (statements: Record<string, string>) => void;
}) {
  const resource = useResource<Concept[]>(query(layer, sourceId, LIMIT));
  const items = resource.data ?? [];
  const last = items.length === LIMIT ? items.at(-1)?.id : undefined;
  const more = useResource<Concept[]>(
    last ? query(layer, sourceId, 1, last) : null,
  );
  // Parents usually sit in another layer, so statements are shared across sections.
  useEffect(() => {
    if (resource.data)
      onLoaded(
        Object.fromEntries(
          resource.data.map((concept) => [
            concept.id,
            assertionText(selected(concept)),
          ]),
        ),
      );
  }, [resource.data]);
  const rows = items.filter((concept) =>
    status === "canonical"
      ? canonical(concept)
      : status === "candidate"
        ? !canonical(concept)
        : true,
  );
  return (
    <section className="export-layer">
      <h2>{levelLabel(layer)}</h2>
      <LoadState resource={resource} empty={rows.length === 0}>
        <p className="table-note">
          {rows.length} {rows.length === 1 ? "item" : "items"}
          {more.data?.length
            ? ` · limit of ${LIMIT} reached; more exist — filter by source to export the rest.`
            : ""}
        </p>
        <ol className="export-items">
          {rows.map((concept) => {
            const version = selected(concept);
            const isCanonical = canonical(concept);
            const excerpts = concept.occurrences.filter(
              (occurrence) =>
                !occurrence.concept_version_id ||
                occurrence.concept_version_id === version?.id,
            );
            const documents = (concept.sources ?? []).filter(
              (source) =>
                (!sourceId || source.id === sourceId) &&
                version &&
                source.version_ids.includes(version.id),
            );
            const parents = concept.relationships.filter(
              (relationship) => relationship.from_concept_id === concept.id,
            );
            return (
              <li key={concept.id} id={`rule-${concept.id}`}>
                <p className="export-statement">
                  <a href={`/concepts/${concept.id}`}>
                    {assertionText(version)}
                  </a>
                </p>
                <p className="cell-caption">
                  {version?.candidate.semantic_type ?? "Type unresolved"} ·{" "}
                  {isCanonical ? (
                    <strong>Canonical</strong>
                  ) : (
                    <strong>Candidate — not approved</strong>
                  )}
                </p>
                {documents.map((source) => (
                  <p className="cell-caption" key={source.id}>
                    Source: {source.name} ·{" "}
                    {source.publisher ?? "Unknown publisher"} ·{" "}
                    {source.location}
                  </p>
                ))}
                {excerpts.slice(0, 1).map((occurrence) => (
                  <blockquote key={occurrence.id}>
                    {occurrence.excerpt}
                  </blockquote>
                ))}
                {excerpts.length > 1 && (
                  <p className="cell-caption">
                    {excerpts.length - 1} further evidence passages on the
                    concept page.
                  </p>
                )}
                {parents.length > 0 && (
                  <p className="cell-caption">
                    {parents.map((relationship, index) => (
                      <span
                        key={`${relationship.kind}-${relationship.to_concept_id}`}
                      >
                        {index > 0 && " · "}
                        {label(relationship.kind)}{" "}
                        <a
                          href={
                            statements[relationship.to_concept_id]
                              ? `#rule-${relationship.to_concept_id}`
                              : `/concepts/${relationship.to_concept_id}`
                          }
                        >
                          {statements[relationship.to_concept_id] ??
                            `concept ${relationship.to_concept_id.slice(0, 8)}`}
                        </a>
                      </span>
                    ))}
                  </p>
                )}
              </li>
            );
          })}
        </ol>
      </LoadState>
    </section>
  );
}
