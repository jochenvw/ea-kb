import { useId, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Heading,
  LoadState,
  Panel,
  SearchBox,
  Empty,
  Status,
} from "@/components/workspace";
import { label, useDebounced, useResource } from "@/lib/api";
import type { Concept, KnowledgeGraph } from "@/types";

export function Explorer() {
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query);
  const matches = useResource<Concept[]>(
    `/api/concepts?limit=20&q=${encodeURIComponent(debounced)}`,
  );
  const selected = new URLSearchParams(location.search).get("concept");
  return (
    <>
      <Heading
        eyebrow="REFERENCE / VISUAL EXPLORER"
        title="Follow the connections."
        description="Move between source evidence, interpretations and recorded relationships. Proposals stay visibly separate from asserted links."
      />
      <Panel
        title="Choose an interpretation"
        description="Search the corpus, then select a starting point. Up to 20 matching interpretations are shown."
      >
        <SearchBox
          name="Find a graph starting point"
          placeholder="Search interpretations…"
          value={query}
          onChange={setQuery}
        />
        <LoadState
          resource={{
            ...matches,
            loading: matches.loading || query !== debounced,
          }}
          empty={matches.data?.length === 0}
        >
          <div className="graph-choices">
            {matches.data?.map((concept) => (
              <a
                key={concept.id}
                aria-current={concept.id === selected ? "true" : undefined}
                href={`/explore?concept=${concept.id}`}
              >
                {concept.versions.at(-1)?.candidate.statement ??
                  "Interpretation"}
                <ArrowUpRight size={14} />
              </a>
            ))}
          </div>
        </LoadState>
      </Panel>
      {(selected || matches.data?.[0]?.id) && (
        <RelationshipGraph id={selected ?? matches.data![0].id} />
      )}
    </>
  );
}

function lines(text: string) {
  const words = text.split(/\s+/);
  const result = [""];
  for (const word of words) {
    if (result.at(-1) && result.at(-1)!.length + word.length > 29)
      result.push("");
    result[result.length - 1] += `${result.at(-1) ? " " : ""}${word}`;
  }
  return result
    .slice(0, 3)
    .map((line, index) =>
      line.length > 31
        ? `${line.slice(0, 28)}…`
        : index === 2 && result.length > 3
          ? `${line.slice(0, 27)}…`
          : line,
    );
}

export function RelationshipGraph({ id }: { id: string }) {
  const resource = useResource<KnowledgeGraph>(
    `/api/concepts/${encodeURIComponent(id)}/graph`,
  );
  const marker = useId().replaceAll(":", "");
  const graph = resource.data;
  const sources = graph?.nodes.filter((node) => node.kind === "source") ?? [];
  const related =
    graph?.nodes.filter(
      (node) => node.kind !== "source" && node.id !== graph.root,
    ) ?? [];
  const height = Math.max(
    280,
    Math.max(sources.length, related.length) * 124 + 80,
  );
  const positions = new Map(
    (graph?.nodes ?? []).map((node) => {
      const column = node.kind === "source" ? sources : related;
      return [
        node.id,
        {
          x: node.kind === "source" ? 16 : node.id === graph?.root ? 368 : 720,
          y:
            node.id === graph?.root
              ? height / 2 - 48
              : 48 + column.indexOf(node) * 124,
        },
      ];
    }),
  );
  return (
    <Panel
      title="Evidence & relationship map"
      description="Concept-level navigation across retained versions, not proof for every version. Click sources for exact evidence or connected interpretations to recenter. Dashed links are proposals, not accepted identity or authority."
    >
      <LoadState resource={resource}>
        {graph && (
          <>
            <div className="graph-legend">
              <span>Source evidence</span>
              <span>Interpretations</span>
              <span>Recorded links / dashed proposals</span>
            </div>
            <div
              className="graph-viewport"
              tabIndex={0}
              role="region"
              aria-label="Scrollable relationship map"
            >
              <svg
                viewBox={`0 0 1000 ${height}`}
                role="group"
                aria-label="Navigable evidence and relationship map"
              >
                <defs>
                  <marker
                    id={marker}
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="7"
                    markerHeight="7"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 0 L 10 5 L 0 10 z" className="graph-arrow" />
                  </marker>
                </defs>
                {graph.edges.map((edge, index) => {
                  const from = positions.get(edge.source);
                  const to = positions.get(edge.target);
                  if (!from || !to) return null;
                  const forward = from.x < to.x;
                  const x1 = from.x + (forward ? 264 : 0),
                    x2 = to.x + (forward ? 0 : 264),
                    y1 = from.y + 48,
                    y2 = to.y + 48;
                  return (
                    <a
                      key={index}
                      href={edge.href}
                      aria-label={`Inspect ${label(edge.kind)} ${label(edge.status)} link`}
                    >
                      <path
                        d={`M ${x1} ${y1} C ${(x1 + x2) / 2} ${y1}, ${(x1 + x2) / 2} ${y2}, ${x2} ${y2}`}
                        className={`graph-edge ${["proposal", "proposed"].includes(edge.status) ? "is-proposal" : ""}`}
                        markerEnd={`url(#${marker})`}
                      />
                      <text
                        x={(x1 + x2) / 2}
                        y={(y1 + y2) / 2 - 8}
                        textAnchor="middle"
                        className="graph-edge-label"
                      >
                        {label(edge.kind).slice(0, 20)}
                      </text>
                    </a>
                  );
                })}
                {graph.nodes.map((node) => {
                  const position = positions.get(node.id)!;
                  const href =
                    node.kind === "source" || node.id === graph.root
                      ? node.href
                      : `/explore?concept=${node.id}`;
                  return (
                    <a
                      key={`${node.kind}-${node.id}`}
                      href={href}
                      aria-label={`${node.kind === "source" ? "Browse source" : node.id === graph.root ? "Inspect interpretation" : "Explore"} ${node.label}`}
                    >
                      <title>{node.label}</title>
                      <rect
                        x={position.x}
                        y={position.y}
                        width="264"
                        height="96"
                        rx="10"
                        className={`graph-node ${node.id === graph.root ? "is-root" : ""}`}
                      />
                      <text
                        x={position.x + 16}
                        y={position.y + 20}
                        className="graph-kind"
                      >
                        {label(node.kind)}
                        {node.id === graph.root ? " / selected" : ""}
                      </text>
                      <text
                        x={position.x + 16}
                        y={position.y + 40}
                        className="graph-label"
                      >
                        {lines(node.label).map((line, index) => (
                          <tspan
                            key={index}
                            x={position.x + 16}
                            dy={index ? 19 : 0}
                          >
                            {line}
                          </tspan>
                        ))}
                      </text>
                    </a>
                  );
                })}
              </svg>
            </div>
            {!related.length && (
              <p className="table-note">
                No connected interpretations are recorded in this neighborhood.
                Unresolved targets are not invented as nodes.
              </p>
            )}
            {graph.truncated && (
              <p className="callout">
                This is a bounded neighborhood: up to 12 related interpretations
                and 8 source documents. More records exist; follow a node or
                inspect the ledger.
              </p>
            )}
            <div className="graph-links" aria-label="Relationship evidence">
              {graph.edges.map((edge, index) => (
                <a key={index} href={edge.href}>
                  <span>{label(edge.kind)}</span>
                  <Status value={edge.status} />
                  <ArrowUpRight size={14} />
                </a>
              ))}
            </div>
            <div className="heading-actions">
              <Button variant="outline" asChild>
                <a href={`/concepts/${id}`}>
                  Inspect selected interpretation
                  <ArrowUpRight />
                </a>
              </Button>
            </div>
            {!graph.nodes.length && <Empty title="No graph records" />}
          </>
        )}
      </LoadState>
    </Panel>
  );
}
