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
  Filter,
} from "@/components/workspace";
import {
  date,
  assertionText,
  assertionMatches,
  sourceMembers,
  levelLabel,
  externalUrl,
  label,
  semanticVersion,
  useDebounced,
  useResource,
} from "@/lib/api";
import type { Concept, Provenance, SourceGroup, Version } from "@/types";
import { RelationshipGraph } from "@/network";
import { OntologyBadge, OntologyDefinition } from "@/ontology";

export function Knowledge() {
  const [level, setLevel] = useState(
    new URLSearchParams(location.search).get("level") ?? "top_level",
  );
  const [sourceId, setSourceId] = useState(
    new URLSearchParams(location.search).get("source_id") ?? "",
  );
  const sources = useResource<SourceGroup[]>("/api/source-groups");
  const members = sourceMembers(sources.data, sourceId);
  const selectedGroup = sources.data?.find(
    (group) =>
      group.id === sourceId ||
      group.documents.some((doc) => doc.item.id === sourceId),
  );
  const [query, setQuery] = useState(
    new URLSearchParams(location.search).get("q") ?? "",
  );
  const debounced = useDebounced(query);
  const filterKey = `${level}\0${sourceId}\0${debounced}`;
  const [pagination, setPagination] = useState<{
    query: string;
    cursors: (string | null)[];
  }>({ query: filterKey, cursors: [null] });
  const cursors = pagination.query === filterKey ? pagination.cursors : [null];
  const cursor = cursors.at(-1);
  const resource = useResource<Concept[]>(
    `/api/concepts?limit=50&q=${encodeURIComponent(debounced)}${level ? `&level=${level}` : ""}${sourceId ? `&source_id=${encodeURIComponent(sourceId)}` : ""}${cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`,
  );
  const rows = resource.data ?? [];
  return (
    <>
      <Heading
        eyebrow="CURATE / INTERPRETATIONS"
        title="Review meaning before adoption."
        description="Compare source-grounded goals and requirements. Check the ontology definition, original evidence and scope; a result is not an adopted enterprise standard."
      >
        <Button variant="outline" asChild>
          <a href="/ontology">
            Concepts & definitions
            <ArrowUpRight />
          </a>
        </Button>
      </Heading>
      <Panel
        title="Interpretation library"
        description="Concise assertion candidates, linked to their original source passages."
      >
        <div className="toolbar">
          <Filter
            name="Architecture level"
            value={level}
            onChange={(value) => {
              setLevel(value);
              const params = new URLSearchParams(location.search);
              params.set("level", value);
              history.replaceState(null, "", `${location.pathname}?${params}`);
            }}
            options={[
              ["top_level", "Goals & guidance"],
              ["implementation", "Platform implementation"],
              ["", "All levels"],
            ]}
          />
          <SearchBox
            value={query}
            onChange={(value) => {
              setQuery(value);
              const params = new URLSearchParams(location.search);
              if (value) params.set("q", value);
              else params.delete("q");
              history.replaceState(null, "", `${location.pathname}?${params}`);
            }}
            name="Search knowledge"
            placeholder="Search statements, e.g. logging or network security…"
          />
          <Filter
            name="Source"
            value={selectedGroup?.id ?? sourceId}
            onChange={(value) => {
              setSourceId(value);
              const params = new URLSearchParams(location.search);
              if (value) params.set("source_id", value);
              else params.delete("source_id");
              history.replaceState(null, "", `${location.pathname}?${params}`);
            }}
            options={[
              ["", "All sources"],
              ...(sources.data ?? [])
                .slice()
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((source): [string, string] => [
                  source.id,
                  `${source.name} · ${source.publisher ?? "Unknown publisher"}`,
                ]),
            ]}
          />
          {selectedGroup?.edition && (
            <Filter
              name="Evidence document"
              value={sourceId === selectedGroup.id ? "" : sourceId}
              onChange={(value) => {
                const next = value || selectedGroup.id;
                setSourceId(next);
                const params = new URLSearchParams(location.search);
                params.set("source_id", next);
                history.replaceState(
                  null,
                  "",
                  `${location.pathname}?${params}`,
                );
              }}
              options={[
                ["", "All documents in this edition"],
                ...selectedGroup.documents.map((doc): [string, string] => [
                  doc.item.id,
                  doc.item.name,
                ]),
              ]}
            />
          )}
        </div>
        <LoadState resource={sources}>
          <span className="sr-only">Source document filters loaded</span>
        </LoadState>
        <p className="table-note">
          {level === "top_level" &&
            "Showing goals and guidance. Choose Platform implementation for Azure baselines. "}
          Goals and framework guidance describe intent. Explicitly
          platform-scoped requirements describe implementation. This is a
          presentation level, not authority or proof of coverage. One assertion
          per row. Source filters select evidenced versions, not an unrelated
          latest interpretation. Browse 50 matches per page. Rephrasing is a
          candidate summary, never a new policy obligation.
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
                <TableHead className="statement-column">Assertion</TableHead>
                <TableHead>Architecture level</TableHead>
                <TableHead>Source / evidence document</TableHead>
                <TableHead>Ontology concept</TableHead>
                <TableHead>Publisher maturity</TableHead>
                <TableHead>Evidence</TableHead>
                <TableHead>
                  <span className="sr-only">Inspect</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((concept) => {
                const version =
                  concept.versions.find(
                    (item) => item.id === concept.matching_version_id,
                  ) ?? concept.versions.at(-1);
                const documents = (concept.sources ?? []).filter(
                  (source) =>
                    (!members.length || members.includes(source.id)) &&
                    version &&
                    source.version_ids.includes(version.id),
                );
                const params = new URLSearchParams();
                params.set("level", level);
                if (version) params.set("version_id", version.id);
                if (sourceId) params.set("source_id", sourceId);
                if (debounced) params.set("q", debounced);
                const href = `/concepts/${concept.id}?${params}`;
                return (
                  <TableRow key={concept.id}>
                    <TableCell>
                      <a className="statement-link" href={href}>
                        {assertionText(version, members)}
                      </a>
                      <span className="cell-caption">
                        {version
                          ? `${Math.round(version.candidate.confidence * 100)}% extraction confidence · ${label(version.candidate.modality)}`
                          : "Unknown"}
                      </span>
                      {(!version?.assertion?.text ||
                        !assertionMatches(version, members)) && (
                        <span className="cell-caption">
                          {version?.assertion?.text === null &&
                          assertionMatches(version, members)
                            ? "Needs clarification · original wording"
                            : "Original wording · rephrasing pending"}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">
                        {levelLabel(concept.levels?.[version?.id ?? ""])}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {documents.length ? (
                        documents.map((source) => {
                          const group = sources.data?.find((group) =>
                            group.documents.some(
                              (doc) => doc.item.id === source.id,
                            ),
                          );
                          const linked = group
                            ? documents.filter((doc) =>
                                group.documents.some(
                                  (member) => member.item.id === doc.id,
                                ),
                              )
                            : [source];
                          if (linked[0]?.id !== source.id) return null;
                          return (
                            <a
                              className="source-citation"
                              key={source.id}
                              href={`/sources/${group?.id ?? source.id}`}
                            >
                              {group?.name ?? source.name}
                              <small>
                                {linked.map((doc) => doc.name).join(" · ")} ·{" "}
                                {source.publisher ?? "Unknown publisher"}
                              </small>
                            </a>
                          );
                        })
                      ) : (
                        <span className="muted">No linked source</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <OntologyBadge name={version?.candidate.semantic_type} />
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
                    <TableCell>
                      {documents.reduce(
                        (count, source) =>
                          count +
                          (source.evidence_counts?.[version?.id ?? ""] ?? 0),
                        0,
                      )}{" "}
                      passages
                    </TableCell>
                    <TableCell>
                      <Button variant="ghost" size="icon" asChild>
                        <a
                          href={href}
                          aria-label={`Inspect ${assertionText(version, members)}`}
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
            setPagination({ query: filterKey, cursors: cursors.slice(0, -1) })
          }
          onNext={() => {
            const last = rows.at(-1)?.id;
            if (last)
              setPagination({ query: filterKey, cursors: [...cursors, last] });
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
  const requested = new URLSearchParams(location.search).get("version_id");
  const sourceId = new URLSearchParams(location.search).get("source_id");
  const groups = useResource<SourceGroup[]>(
    sourceId ? "/api/source-groups" : null,
  );
  const members = sourceMembers(groups.data, sourceId);
  const back = new URLSearchParams(location.search);
  back.delete("version_id");
  const selected = requested
    ? concept.data?.versions.find((item) => item.id === requested)
    : concept.data?.versions.at(-1);
  const version =
    selected && !assertionMatches(selected, members)
      ? { ...selected, assertion: null }
      : selected;
  const evidence = provenance.data?.filter(
    (item) =>
      item.occurrence.concept_version_id === version?.id &&
      (!members.length || members.includes(item.source.item.id)),
  );
  return (
    <>
      <Button variant="ghost" size="sm" asChild className="back-link">
        <a href={`/knowledge${back.size ? `?${back}` : ""}`}>
          <ArrowLeft />
          Interpretation library
        </a>
      </Button>
      <Heading
        eyebrow="CONCEPT DETAIL"
        title="Interpretation & evidence"
        description="Verify meaning against the source, validate relationships, then inspect the scoped decisions governing this version."
      />
      {sourceId && (
        <LoadState resource={groups}>
          <span className="sr-only">Source membership loaded</span>
        </LoadState>
      )}
      <LoadState resource={concept}>
        {concept.data && !version && (
          <Empty
            title="Requested version not found"
            description="No unrelated interpretation has been substituted."
          />
        )}
        {concept.data && version && (
          <>
            <Panel
              title="Assertion"
              description="Source-grounded assertion; verify against the original quote and context."
            >
              <p className="concept-statement">{assertionText(version)}</p>
              {!version.assertion?.text && (
                <p className="table-note">
                  {version.assertion?.uncertainty ??
                    "Original wording shown; concise rephrasing has not run."}
                </p>
              )}
              {version.assertion?.text && (
                <details className="technical">
                  <summary>Original extracted wording</summary>
                  <p>{version.candidate.statement}</p>
                  <p className="table-note">
                    Assertion phrasing: {version.assertion.model_id} ·{" "}
                    {version.assertion.prompt_version}. Not enterprise approval.
                  </p>
                </details>
              )}
              {version.assertion?.text && version.assertion.uncertainty && (
                <p className="callout">{version.assertion.uncertainty}</p>
              )}
              {requested && (
                <p className="table-note">
                  Viewing source-linked version recorded{" "}
                  {date(version.recorded_at)}. Other interpretations remain in
                  version history.
                </p>
              )}
              <Button variant="outline" size="sm" asChild>
                <a href={`/explore?concept=${id}`}>
                  Explore visual relationships
                  <ArrowUpRight />
                </a>
              </Button>
              <div className="tag-row">
                <Badge variant="outline">
                  {levelLabel(concept.data.levels?.[version.id])}
                  {concept.data.levels?.[version.id] === "implementation" &&
                  version.candidate.scope.platform
                    ? ` · ${version.candidate.scope.platform}`
                    : ""}
                </Badge>
                <OntologyBadge name={version.candidate.semantic_type} />
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
              <OntologyDefinition name={version.candidate.semantic_type} />
            </Panel>
            <Tabs defaultValue="evidence" className="detail-tabs">
              <TabsList aria-label="Concept information">
                <TabsTrigger value="evidence">Source evidence</TabsTrigger>
                <TabsTrigger value="validation">Visual validation</TabsTrigger>
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
                  <LoadState resource={provenance}>
                    {evidence?.length === 0 ? (
                      <Empty
                        title="No evidence recorded for this version"
                        description="No passage from another version or source has been substituted. Check the version history before relying on this interpretation."
                      />
                    ) : (
                      <div className="evidence-list">
                        {evidence?.map((item) => (
                          <Evidence key={item.occurrence.id} item={item} />
                        ))}
                      </div>
                    )}
                  </LoadState>
                </Panel>
              </TabsContent>
              <TabsContent value="validation">
                <RelationshipGraph id={id} />
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
                    {concept.data.authority_states.some(
                      (state) => state.concept_version_id === version.id,
                    ) ? (
                      concept.data.authority_states
                        .filter(
                          (state) => state.concept_version_id === version.id,
                        )
                        .map((state, index) => (
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
                        description="No enterprise approval or canonical adoption has been established for this version."
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
                  versionId={version.id}
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
                      <p>{assertionText(item)}</p>
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
  const points = Array.from(item.segment.text ?? "");
  const { start, end, excerpt } = item.occurrence;
  const valid =
    start >= 0 &&
    end <= points.length &&
    points.slice(start, end).join("") === excerpt;
  const from = Math.max(0, start - 240);
  const to = Math.min(points.length, end + 240);
  return (
    <article className="evidence">
      <div className="evidence-heading">
        <FileText size={18} />
        <div>
          <h3>
            <a href={`/sources/${item.source.item.id}`}>
              {item.source.item.name}
            </a>
          </h3>
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
      <p className="eyebrow">Original quote</p>
      <blockquote>{excerpt}</blockquote>
      {valid ? (
        <div className="source-context">
          <p className="eyebrow">Surrounding source context</p>
          {item.context_before && (
            <div>
              <small>
                Previous retained passage ·{" "}
                {item.context_before.heading ?? "Source text"}
              </small>
              <p>
                {Array.from(item.context_before.text).length > 320 ? "…" : ""}
                {Array.from(item.context_before.text).slice(-320).join("")}
              </p>
            </div>
          )}
          <p>
            {from > 0 ? "…" : ""}
            {points.slice(from, start).join("")}
            <mark>{excerpt}</mark>
            {points.slice(end, to).join("")}
            {to < points.length ? "…" : ""}
          </p>
          {item.context_after && (
            <div>
              <small>
                Next retained passage ·{" "}
                {item.context_after.heading ?? "Source text"}
              </small>
              <p>
                {Array.from(item.context_after.text).slice(0, 320).join("")}
                {Array.from(item.context_after.text).length > 320 ? "…" : ""}
              </p>
            </div>
          )}
          {start === 0 &&
            end === points.length &&
            !item.context_before &&
            !item.context_after && (
              <p className="table-note">
                The quote spans the whole retained section; no additional nearby
                text was retained.
              </p>
            )}
          {(from > 0 || to < points.length) && (
            <details className="technical">
              <summary>Read full retained section</summary>
              <p>{item.segment.text}</p>
            </details>
          )}
        </div>
      ) : (
        <p className="table-note">
          Surrounding context is unavailable for these evidence offsets.
        </p>
      )}
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
  const version = resource.data?.versions.at(-1);
  return (
    <LoadState resource={resource}>
      <a className="related-concept" href={`/concepts/${id}`}>
        <FileText size={18} aria-hidden="true" />{" "}
        <span>
          {assertionText(version)}
          <small>Inspect exact evidence and scoped authority</small>
        </span>
        <ArrowUpRight size={16} />
      </a>
    </LoadState>
  );
}

function CanonicalProposal({
  concept,
  versionId,
  governance,
}: {
  concept: Concept;
  versionId: string;
  governance: boolean;
}) {
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
              defaultValue={versionId}
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
