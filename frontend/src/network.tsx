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
  Filter,
} from "@/components/workspace";
import {
  label,
  levelLabel,
  assertionText,
  useDebounced,
  useResource,
} from "@/lib/api";
import type { Concept, KnowledgeGraph } from "@/types";

export function Explorer() {
  const [level, setLevel] = useState("");
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query);
  const matches = useResource<Concept[]>(
    `/api/concepts?limit=20&q=${encodeURIComponent(debounced)}${level ? `&level=${level}` : ""}`,
  );
  const selected = new URLSearchParams(location.search).get("concept");
  return (
    <>
      <Heading
        eyebrow="VALIDATE / RELATIONSHIPS"
        title="Validate meaning and connections."
        description="Inspect one interpretation's neighborhood, not an enterprise coverage map. Check relationship direction and evidence; proposals are not accepted links."
      >
        <Button variant="outline" asChild>
          <a href="/ontology">
            Relationship definitions
            <ArrowUpRight />
          </a>
        </Button>
      </Heading>
      {selected && <RelationshipGraph id={selected} />}
      <Panel
        title={
          selected ? "Change the starting point" : "Choose an interpretation"
        }
        description="Search the corpus, then select a starting point. Up to 20 matching interpretations are shown."
      >
        <SearchBox
          name="Find a graph starting point"
          placeholder="Search interpretations…"
          value={query}
          onChange={setQuery}
        />
        <Filter
          name="Architecture level"
          value={level}
          onChange={setLevel}
          options={[
            ["", "All levels"],
            ["top_level", "Goals & guidance"],
            ["implementation", "Platform implementation"],
          ]}
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
                <span>
                  {assertionText(
                    concept.versions.find(
                      (v) => v.id === concept.matching_version_id,
                    ) ?? concept.versions.at(-1),
                  )}
                  <small className="cell-caption">
                    {levelLabel(
                      concept.levels?.[
                        concept.matching_version_id ??
                          concept.versions.at(-1)?.id ??
                          ""
                      ],
                    )}
                  </small>
                </span>
                <ArrowUpRight size={14} />
              </a>
            ))}
          </div>
        </LoadState>
      </Panel>
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
  const [showProposals, setShowProposals] = useState(false);
  const resource = useResource<KnowledgeGraph>(
    `/api/concepts/${encodeURIComponent(id)}/graph`,
  );
  const marker = useId().replaceAll(":", "");
  const graph = resource.data;
  const edges =
    graph?.edges.filter(
      (edge) =>
        showProposals || !["proposal", "proposed"].includes(edge.status),
    ) ?? [];
  const visible = new Set([
    id,
    ...edges.flatMap((edge) => [edge.source, edge.target]),
  ]);
  const nodes = graph?.nodes.filter((node) => visible.has(node.id)) ?? [];
  const related = nodes.filter(
    (node) => node.kind !== "source" && node.id !== graph?.root,
  );
  const positions = new Map<string, { x: number; y: number }>();
  let height = 0;
  const lanes = [
    { key: "top_level", title: "Goals & guidance" },
    { key: "implementation", title: "Platform implementation" },
    ...(nodes.some((node) => node.kind !== "source" && !node.level)
      ? [{ key: "unknown", title: "Level not classified" }]
      : []),
    { key: "source", title: "Source evidence" },
  ].map((lane) => {
    const members = nodes.filter((node) =>
      lane.key === "source"
        ? node.kind === "source"
        : node.kind !== "source" && (node.level ?? "unknown") === lane.key,
    );
    const y = height;
    const size = Math.max(1, Math.ceil(members.length / 3)) * 132 + 52;
    members.forEach((node, index) =>
      positions.set(node.id, {
        x: 32 + (index % 3) * 320,
        y: y + 44 + Math.floor(index / 3) * 132,
      }),
    );
    height += size + 24;
    return { ...lane, y, size, empty: !members.length };
  });
  return (
    <Panel
      title="Evidence & relationship map"
      description="Concept-level context across retained versions, not proof for a selected version. Goals and guidance sit above platform implementations. Published mappings link whole controls, not clause coverage, equivalence, compliance or company alignment. Dashed proposals require review."
    >
      <LoadState resource={resource}>
        {graph && (
          <>
            <div className="graph-legend">
              <span>Intent above implementation</span>
              <Button
                variant="outline"
                size="sm"
                aria-pressed={showProposals}
                onClick={() => setShowProposals(!showProposals)}
              >
                {showProposals
                  ? "Hide review proposals"
                  : "Show review proposals"}
              </Button>
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
                {lanes.map((lane) => (
                  <g key={lane.key}>
                    <rect
                      x="12"
                      y={lane.y}
                      width="976"
                      height={lane.size}
                      className="graph-lane"
                    />
                    <text x="32" y={lane.y + 26} className="graph-lane-title">
                      {lane.title}
                    </text>
                    {lane.empty && (
                      <text x="32" y={lane.y + 80} className="graph-kind">
                        {lane.key === "implementation"
                          ? "No recorded platform implementation linked."
                          : "No recorded nodes at this level."}
                      </text>
                    )}
                  </g>
                ))}
                {edges.map((edge, index) => {
                  const from = positions.get(edge.source);
                  const to = positions.get(edge.target);
                  if (!from || !to) return null;
                  const sameRow = from.y === to.y;
                  const forward = from.x < to.x;
                  const below = from.y < to.y;
                  const x1 = sameRow
                      ? from.x + (forward ? 264 : 0)
                      : from.x + 132,
                    x2 = sameRow ? to.x + (forward ? 0 : 264) : to.x + 132,
                    y1 = sameRow ? from.y + 48 : from.y + (below ? 96 : 0),
                    y2 = sameRow ? to.y + 48 : to.y + (below ? 0 : 96);
                  const curve = sameRow
                    ? `M ${x1} ${y1} C ${(x1 + x2) / 2} ${y1}, ${(x1 + x2) / 2} ${y2}, ${x2} ${y2}`
                    : `M ${x1} ${y1} V ${y1 + (below ? 12 : -12)} H ${956 + (index % 6) * 5} V ${y2 + (below ? -12 : 12)} H ${x2} V ${y2}`;
                  return (
                    <a
                      key={index}
                      href={edge.href}
                      aria-label={`Inspect ${label(edge.kind)} ${label(edge.status)} link`}
                    >
                      <title>
                        {label(edge.kind)} · {label(edge.status)}
                      </title>
                      <path
                        d={curve}
                        className={`graph-edge ${["proposal", "proposed"].includes(edge.status) ? "is-proposal" : ""}`}
                        markerEnd={`url(#${marker})`}
                      />
                    </a>
                  );
                })}
                {nodes.map((node) => {
                  const position = positions.get(node.id)!;
                  const href =
                    ["source", "framework_control"].includes(node.kind) ||
                    node.id === graph.root
                      ? node.href
                      : `/explore?concept=${node.id}`;
                  return (
                    <a
                      key={`${node.kind}-${node.id}`}
                      href={href}
                      aria-label={`${node.kind === "source" ? "Browse source" : node.kind === "framework_control" ? "Browse native control" : node.id === graph.root ? "Inspect interpretation" : "Explore"} ${node.label}`}
                    >
                      <title>
                        {levelLabel(node.level)}: {node.label}
                      </title>
                      <rect
                        x={position.x}
                        y={position.y}
                        width="264"
                        height="96"
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
                No implementation or alignment link is recorded in this
                neighborhood. Missing goal alignments and unresolved targets are
                not invented.
              </p>
            )}
            {graph.truncated && (
              <p className="callout">
                This is a bounded neighborhood: up to 12 related records and 8
                source documents. More records exist; follow a node or inspect
                the ledger.
              </p>
            )}
            <div className="graph-links" aria-label="Relationship evidence">
              {edges.map((edge, index) => (
                <div className="graph-record" key={index}>
                  <a key={index} href={edge.href}>
                    <span>
                      {label(edge.kind)}
                      {edge.evidence
                        ? ` · ${nodes.find((node) => node.id === edge.target)?.label ?? ""}`
                        : ""}
                    </span>
                    <Status value={edge.status} />
                    <ArrowUpRight size={14} />
                  </a>
                  {(edge.note || edge.evidence) && (
                    <details className="mapping-evidence">
                      <summary>Link basis & evidence</summary>
                      {edge.note && <p>{edge.note}</p>}
                      {edge.evidence && (
                        <blockquote>{edge.evidence.excerpt}</blockquote>
                      )}
                    </details>
                  )}
                </div>
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
