import { useState } from "react";
import { ArrowLeft, ArrowUpRight, FileText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Heading,
  LoadState,
  Panel,
  SearchBox,
  CursorPager,
  ScopeInfo,
  Status,
  Technical,
  Empty,
} from "@/components/workspace";
import {
  date,
  externalUrl,
  label,
  semanticVersion,
  useDebounced,
  useResource,
} from "@/lib/api";
import type { Concept, Provenance, Version } from "@/types";

export function Knowledge() {
  const [query, setQuery] = useState(
    new URLSearchParams(location.search).get("q") ?? "",
  );
  const debounced = useDebounced(query);
  const [pagination, setPagination] = useState<{
    query: string;
    cursors: (string | null)[];
  }>({ query, cursors: [null] });
  const cursors = pagination.query === debounced ? pagination.cursors : [null];
  const cursor = cursors.at(-1);
  const resource = useResource<Concept[]>(
    `/api/concepts?limit=50&q=${encodeURIComponent(debounced)}${cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`,
  );
  const rows = resource.data ?? [];
  return (
    <>
      <Heading
        eyebrow="KNOWLEDGE LIBRARY"
        title="Find the knowledge. Follow the evidence."
        description="Search source-grounded interpretations. A result is not a declaration of enterprise applicability."
      />
      <Panel
        title="Knowledge inventory"
        description="Objective and requirement candidates, with exact source evidence."
      >
        <div className="toolbar">
          <SearchBox
            value={query}
            onChange={setQuery}
            name="Search knowledge"
            placeholder="Search statements, e.g. logging or network security…"
          />
        </div>
        <p className="table-note">
          Search across the corpus and page through all matches, 50 at a time.
          Matches may occur in retained versions; inspect version history when
          needed.
        </p>
        <LoadState
          resource={{
            ...resource,
            loading: resource.loading || debounced !== query,
          }}
          empty={rows.length === 0}
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="statement-column">Statement</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Publisher maturity</TableHead>
                <TableHead>Evidence</TableHead>
                <TableHead>
                  <span className="sr-only">Inspect</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((concept) => {
                const version = concept.versions.at(-1);
                return (
                  <TableRow key={concept.id}>
                    <TableCell>
                      <a
                        className="statement-link"
                        href={`/concepts/${concept.id}`}
                      >
                        {version?.candidate.statement ??
                          "No interpretation recorded"}
                      </a>
                      <span className="cell-caption">
                        {version
                          ? `${Math.round(version.candidate.confidence * 100)}% extraction confidence · ${label(version.candidate.modality)}`
                          : "Unknown"}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">
                        {label(version?.candidate.kind ?? "unknown")}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Status
                        value={
                          version?.publication.publication_status ?? "unknown"
                        }
                      />
                      <span className="cell-caption">
                        Not enterprise approval
                      </span>
                    </TableCell>
                    <TableCell>{concept.occurrences.length} passages</TableCell>
                    <TableCell>
                      <Button variant="ghost" size="icon" asChild>
                        <a
                          href={`/concepts/${concept.id}`}
                          aria-label={`Inspect ${version?.candidate.statement ?? "concept"}`}
                        >
                          <ArrowUpRight />
                        </a>
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </LoadState>
        <CursorPager
          page={cursors.length}
          count={rows.length}
          disabled={resource.loading || !!resource.error || query !== debounced}
          onPrevious={() =>
            setPagination({ query: debounced, cursors: cursors.slice(0, -1) })
          }
          onNext={() => {
            const last = rows.at(-1)?.id;
            if (last)
              setPagination({ query: debounced, cursors: [...cursors, last] });
          }}
        />
      </Panel>
    </>
  );
}

export function ConceptPage({
  id,
  governance,
}: {
  id: string;
  governance: boolean;
}) {
  const concept = useResource<Concept>(
    `/api/concepts/${encodeURIComponent(id)}`,
  );
  const provenance = useResource<Provenance[]>(
    `/api/concepts/${encodeURIComponent(id)}/provenance`,
  );
  const version = concept.data?.versions.at(-1);
  return (
    <>
      <Button variant="ghost" size="sm" asChild className="back-link">
        <a href="/knowledge">
          <ArrowLeft />
          Knowledge library
        </a>
      </Button>
      <Heading
        eyebrow="CONCEPT DETAIL"
        title="Interpretation & evidence"
        description="The candidate, its original passages and the decisions that govern its use."
      />
      <LoadState resource={concept}>
        {concept.data && version && (
          <>
            <Panel
              title={label(version.candidate.kind)}
              description="Extracted interpretation; verify against the exact source."
            >
              <p className="concept-statement">{version.candidate.statement}</p>
              <div className="tag-row">
                <Badge variant="secondary">
                  {label(version.candidate.modality)}
                </Badge>
                <Badge variant="outline">
                  {Math.round(version.candidate.confidence * 100)}% extraction
                  confidence
                </Badge>
                <Badge variant="outline">
                  Publisher: {label(version.publication.publication_status)}
                </Badge>
              </div>
              {version.candidate.uncertainty && (
                <p className="callout">{version.candidate.uncertainty}</p>
              )}
            </Panel>
            <Tabs defaultValue="evidence" className="detail-tabs">
              <TabsList aria-label="Concept information">
                <TabsTrigger value="evidence">Source evidence</TabsTrigger>
                <TabsTrigger value="governance">Scope & governance</TabsTrigger>
                <TabsTrigger value="history">
                  Versions & relationships
                </TabsTrigger>
              </TabsList>
              <TabsContent value="evidence">
                <Panel
                  title="Exact evidence and provenance"
                  description="Original quotations are not summaries. Offsets refer to the retained segment."
                >
                  <LoadState
                    resource={provenance}
                    empty={provenance.data?.length === 0}
                  >
                    <div className="evidence-list">
                      {provenance.data?.map((item) => (
                        <Evidence key={item.occurrence.id} item={item} />
                      ))}
                    </div>
                  </LoadState>
                </Panel>
              </TabsContent>
              <TabsContent value="governance">
                <div className="two-column">
                  <Panel title="Candidate scope">
                    <ScopeInfo scope={version.candidate.scope} />
                    <p className="table-note">
                      Missing scope does not mean universally applicable.
                    </p>
                  </Panel>
                  <Panel
                    title="Scoped authority"
                    description="Publication maturity, approval and canonical adoption are separate, scoped decisions."
                  >
                    {concept.data.authority_states.length ? (
                      concept.data.authority_states.map((state, index) => (
                        <div key={index} className="record-block">
                          <div className="tag-row">
                            <Badge variant="outline">
                              Governance: {label(state.governance_status)}
                            </Badge>
                            <Badge variant="outline">
                              Canonical: {label(state.canonical_status)}
                            </Badge>
                          </div>
                          <ScopeInfo scope={state.scope} />
                          <p className="table-note">
                            Effective period:{" "}
                            {state.effective_from ?? "Unspecified"} to{" "}
                            {state.effective_until ?? "Unspecified"}.
                          </p>
                          <Technical
                            value={{ version_id: state.concept_version_id }}
                            title="Version reference"
                          />
                        </div>
                      ))
                    ) : (
                      <Empty
                        title="No authority recorded"
                        description="No enterprise approval or canonical adoption has been established."
                      />
                    )}
                    <Technical
                      value={concept.data.authority_events}
                      title="Authority event history"
                    />
                  </Panel>
                </div>
                <CanonicalProposal
                  concept={concept.data}
                  governance={governance}
                />
              </TabsContent>
              <TabsContent value="history">
                <Panel
                  title="Version history"
                  description="Publisher edition labels are not invented semantic versions."
                >
                  {concept.data.versions.map((item) => (
                    <div className="record-block" key={item.id}>
                      <div className="tag-row">
                        <Badge variant="outline">
                          SemVer: {semanticVersion(item.semantic_version)}
                        </Badge>
                        <Badge variant="secondary">
                          Publisher version:{" "}
                          {item.publication.publisher_version ?? "Unknown"}
                        </Badge>
                        <span className="muted">{date(item.recorded_at)}</span>
                      </div>
                      <p>{item.candidate.statement}</p>
                      <Technical value={item} title="Version details" />
                    </div>
                  ))}
                </Panel>
                <Panel title="Relationships">
                  {concept.data.relationships.length ? (
                    concept.data.relationships.map((edge, index) => (
                      <div className="record-block" key={index}>
                        <div className="tag-row">
                          <Badge variant="secondary">{label(edge.kind)}</Badge>
                          <Status value={edge.status} />
                        </div>
                        <a
                          href={`/concepts/${edge.from_concept_id === id ? edge.to_concept_id : edge.from_concept_id}`}
                        >
                          Inspect related concept
                          <ArrowUpRight size={14} />
                        </a>
                      </div>
                    ))
                  ) : (
                    <Empty
                      title="No relationships recorded"
                      description="No relationship has been asserted for this concept."
                    />
                  )}
                </Panel>
                <ConceptLineage id={id} />
              </TabsContent>
            </Tabs>
            <Technical value={{ concept_id: id }} title="Concept identifier" />
          </>
        )}
      </LoadState>
    </>
  );
}

function Evidence({ item }: { item: Provenance }) {
  return (
    <article className="evidence">
      <div className="evidence-heading">
        <FileText size={18} />
        <div>
          <h3>{item.source.item.name}</h3>
          <p>
            {item.segment.heading ?? "Source passage"}
            {item.segment.location.page != null
              ? ` · Page ${String(item.segment.location.page)}`
              : ""}
          </p>
        </div>
        {externalUrl(item.source.item.location) && (
          <Button asChild size="icon" variant="ghost">
            <a
              href={externalUrl(item.source.item.location)}
              target="_blank"
              rel="noreferrer"
              aria-label="Open publisher location"
            >
              <ArrowUpRight />
            </a>
          </Button>
        )}
      </div>
      <blockquote>{item.occurrence.excerpt}</blockquote>
      <div className="tag-row">
        <Badge variant="outline">
          Revision maturity:{" "}
          {label(item.revision.publication.publication_status)}
        </Badge>
        <Badge variant="outline">
          Current source: {label(item.source.status)}
        </Badge>
      </div>
      <p className="table-note">
        Exact segment offsets {item.occurrence.start}–{item.occurrence.end} ·
        Revision recorded {date(item.revision.recorded_at)}
      </p>
      <Technical
        value={{
          source_location: item.source.item.location,
          content_hash: item.revision.content_hash,
          revision_id: item.revision.id,
          segment_id: item.segment.id,
          location: item.segment.location,
          extraction: item.occurrence.extraction_metadata,
        }}
        title="Provenance & extraction metadata"
      />
    </article>
  );
}

function ConceptLineage({ id }: { id: string }) {
  const resource = useResource<{ origins: string[]; dependents: string[] }>(
    `/api/concepts/${id}/lineage`,
  );
  return (
    <Panel
      title="Concept lineage"
      description="Recorded derivation links, not inferred equivalence."
    >
      <LoadState resource={resource}>
        {resource.data && (
          <>
            <h3>Origins</h3>
            {resource.data.origins.length ? (
              resource.data.origins.map((origin) => (
                <a
                  className="external-link"
                  key={origin}
                  href={`/concepts/${origin}`}
                >
                  Inspect origin {origin.slice(0, 8)}
                  <ArrowUpRight size={14} />
                </a>
              ))
            ) : (
              <p className="muted">No origins recorded.</p>
            )}
            <h3>Dependents</h3>
            {resource.data.dependents.length ? (
              resource.data.dependents.map((dependent) => (
                <a
                  className="external-link"
                  key={dependent}
                  href={`/concepts/${dependent}`}
                >
                  Inspect dependent {dependent.slice(0, 8)}
                  <ArrowUpRight size={14} />
                </a>
              ))
            ) : (
              <p className="muted">No dependents recorded.</p>
            )}
          </>
        )}
      </LoadState>
    </Panel>
  );
}

export function RelatedConcept({ id }: { id: string }) {
  const resource = useResource<Concept>(`/api/concepts/${id}`);
  const candidate = resource.data?.versions.at(-1)?.candidate;
  return (
    <LoadState resource={resource}>
      <a className="related-concept" href={`/concepts/${id}`}>
        <FileText size={18} aria-hidden="true" />{" "}
        <span>
          {candidate?.statement ?? "No interpretation recorded"}
          <small>Inspect exact evidence and scoped authority</small>
        </span>
        <ArrowUpRight size={16} />
      </a>
    </LoadState>
  );
}

function CanonicalProposal({
  concept,
  governance,
}: {
  concept: Concept;
  governance: boolean;
}) {
  const latest = concept.versions.at(-1)!;
  return (
    <Panel
      title="Canonical adoption"
      description="An explicit proposal, never an automatic promotion."
    >
      {!governance ? (
        <p className="muted">
          HTTP governance is disabled in this workspace. An operator must
          configure EKB_GOVERNANCE_TOKEN before submitting a reviewed proposal.
        </p>
      ) : (
        <details className="technical">
          <summary>Propose scoped canonical adoption</summary>
          <form
            action="/review/canonical"
            method="post"
            className="governance-form"
          >
            <input type="hidden" name="concept_id" value={concept.id} />
            <Label htmlFor="canonical-version">Version to propose</Label>
            <select
              className="control"
              id="canonical-version"
              name="version_id"
              defaultValue={latest.id}
            >
              {concept.versions.map((version: Version) => (
                <option key={version.id} value={version.id}>
                  {semanticVersion(version.semantic_version)} ·{" "}
                  {date(version.recorded_at)}
                </option>
              ))}
            </select>
            <Label htmlFor="canonical-scope">
              Explicit applicability scope (JSON)
            </Label>
            <Textarea
              id="canonical-scope"
              name="scope"
              required
              defaultValue="{}"
              rows={4}
            />
            <p className="muted">
              Creates a review proposal, not approval. Specify the intended
              scope; it is never inferred from the source.
            </p>
            <Label className="checkbox-label">
              <Input type="checkbox" name="allow_prerelease" value="true" />
              Explicitly permit a prerelease source
            </Label>
            <Button type="submit">Create canonical proposal</Button>
            <p className="table-note">
              The browser requests your operator name and governance token using
              HTTP Basic authentication. No credentials are stored by this UI.
            </p>
          </form>
        </details>
      )}
    </Panel>
  );
}
