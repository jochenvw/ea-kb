import { useState } from "react";
import { ArrowLeft, ArrowUpRight, Network } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  Heading,
  LoadState,
  Panel,
  SearchBox,
  Filter,
  Pager,
  Status,
  Technical,
  usePage,
  CursorPager,
  Metric,
  Empty,
} from "@/components/workspace";
import {
  date,
  assertionText,
  externalUrl,
  label,
  levelLabel,
  semanticVersion,
  useResource,
} from "@/lib/api";
import type { Source, SourceInspection } from "@/types";
import { OntologyBadge } from "@/ontology";

export function Sources() {
  const resource = useResource<Source[]>("/api/sources");
  const [query, setQuery] = useState("");
  const [format, setFormat] = useState("");
  const [selected, setSelected] = useState<Source | null>(null);
  const overview = selected;
  const items = (resource.data ?? []).filter(
    (source) =>
      (!format || source.item.format === format) &&
      `${source.item.name} ${source.item.metadata.publisher ?? ""} ${source.item.metadata.publisher_version ?? ""} ${source.item.location}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const { page, setPage, rows } = usePage(items);
  return (
    <>
      <Heading
        eyebrow="CURATE / SOURCES"
        title="Establish the source context."
        description="Inspect strategy documents, standards and platform guidance before relying on extracted statements. Publisher editions remain separate; originals and retrieval history are retained."
      />
      <Panel
        title="Registered sources"
        description="Editions stay separate. Original document identities, retrieval history and exact evidence are preserved."
      >
        <div className="toolbar">
          <SearchBox
            value={query}
            name="Search sources"
            placeholder="Search source names, publishers or locations…"
            onChange={(value) => {
              setQuery(value);
              setPage(0);
            }}
          />
          <Filter
            name="Source format"
            value={format}
            onChange={(value) => {
              setFormat(value);
              setPage(0);
            }}
            options={[
              ["", "All formats"],
              ...[
                ...new Set(
                  (resource.data ?? []).map((source) => source.item.format),
                ),
              ]
                .sort()
                .map((value): [string, string] => [value, value.toUpperCase()]),
            ]}
          />
        </div>
        <LoadState resource={resource}>
          {items.length === 0 && (
            <Empty
              title={
                resource.data?.length
                  ? "No matching sources"
                  : "No sources registered"
              }
              description={
                resource.data?.length
                  ? "Clear the search or format filter to see other registered sources."
                  : "Configure sources and run ingestion with the CLI. Discovery and extraction do not establish enterprise approval."
              }
            />
          )}
          {items.length > 0 && (
            <Sheet
              open={selected !== null}
              onOpenChange={(open) => {
                if (!open) setSelected(null);
              }}
            >
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="statement-column">Source</TableHead>
                    <TableHead>Format</TableHead>
                    <TableHead>Retrieval state</TableHead>
                    <TableHead>Reference document maturity</TableHead>
                    <TableHead>
                      <span className="sr-only">Details</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((source) => (
                    <TableRow key={source.item.id}>
                      <TableCell>
                        <a
                          className="statement-link"
                          href={`/sources/${source.item.id}`}
                        >
                          {source.item.name}
                        </a>
                        <span className="cell-caption truncate-location">
                          {String(
                            source.item.metadata.publisher ??
                              "Unknown publisher",
                          )}
                          {source.publication.publisher_version
                            ? ` · ${source.publication.publisher_version}`
                            : ""}
                        </span>
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary">
                          {source.item.format.toUpperCase()}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Status value={source.status} />
                        {source.item.metadata.metadata_only === true && (
                          <span className="cell-caption">Metadata only</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <Status value={source.publication.publication_status} />
                      </TableCell>
                      <TableCell>
                        <SheetTrigger asChild>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setSelected(source)}
                            aria-label={`Inspect source ${source.item.name}`}
                          >
                            Inspect
                          </Button>
                        </SheetTrigger>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <SheetContent className="detail-sheet">
                <SheetHeader>
                  <SheetTitle>Source details</SheetTitle>
                  <SheetDescription>
                    Metadata describes this source, not enterprise adoption.
                  </SheetDescription>
                </SheetHeader>
                {selected && overview && (
                  <div className="sheet-body">
                    <h2>{selected.item.name}</h2>
                    <Button asChild>
                      <a href={`/sources/${selected.item.id}`}>
                        Review extracted interpretations
                        <ArrowUpRight />
                      </a>
                    </Button>
                    <dl className="metadata">
                      <div>
                        <dt>Publisher</dt>
                        <dd>
                          {String(
                            selected.item.metadata.publisher ?? "Unknown",
                          )}
                        </dd>
                      </div>
                      <div>
                        <dt>Reference document checked</dt>
                        <dd>{date(overview.checked_at)}</dd>
                      </div>
                      <div>
                        <dt>Reference document maturity</dt>
                        <dd>{overview.publication.publication_status}</dd>
                      </div>
                      <div>
                        <dt>Publisher version</dt>
                        <dd>
                          {overview.publication.publisher_version ?? "Unknown"}
                        </dd>
                      </div>
                      <div>
                        <dt>Semantic version</dt>
                        <dd>
                          {semanticVersion(
                            overview.publication.semantic_version,
                          )}
                        </dd>
                      </div>
                      <div>
                        <dt>Location</dt>
                        <dd>{overview.item.location}</dd>
                      </div>
                    </dl>
                    {overview.error && (
                      <p className="callout">{overview.error}</p>
                    )}
                    {externalUrl(overview.item.location) && (
                      <Button asChild variant="outline">
                        <a
                          href={externalUrl(overview.item.location)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Open publisher location
                          <ArrowUpRight />
                        </a>
                      </Button>
                    )}
                    <Technical
                      value={selected}
                      title="Source metadata & identifiers"
                    />
                  </div>
                )}
              </SheetContent>
            </Sheet>
          )}
          {items.length > 0 && (
            <Pager page={page} count={items.length} onChange={setPage} />
          )}
        </LoadState>
      </Panel>
    </>
  );
}

export function SourcePage({ id }: { id: string }) {
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors.at(-1);
  const resource = useResource<SourceInspection>(
    `/api/sources/${encodeURIComponent(id)}?limit=50${cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`,
  );
  const data = resource.data;
  return (
    <>
      <Button variant="ghost" size="sm" asChild className="back-link">
        <a href="/sources">
          <ArrowLeft />
          Source registry
        </a>
      </Button>
      <Heading
        eyebrow="CURATE / SOURCE"
        title={data?.source.item.name ?? "Source detail"}
        description="Check the publisher and edition, then review the extracted meaning against exact source evidence. Retrieval and publication are not enterprise adoption."
      >
        {data && externalUrl(data.source.item.location) && (
          <Button variant="outline" asChild>
            <a
              href={externalUrl(data.source.item.location)}
              target="_blank"
              rel="noreferrer"
            >
              Publisher location
              <ArrowUpRight />
            </a>
          </Button>
        )}
      </Heading>
      <LoadState resource={resource}>
        {data && (
          <>
            <div className="metrics-grid">
              <Metric
                title="Retained revisions"
                value={data.revisions}
                detail="Original retrieval history"
              />
              <Metric
                title="Parsed segments"
                value={data.segments}
                detail="Across retained revisions"
              />
              <Metric
                title="Extracted versions"
                value={data.versions}
                detail="Evidenced in this source, not global latest versions"
              />
              <Metric
                title="Evidence passages"
                value={data.occurrences}
                detail="Exact source-bound occurrences"
              />
            </div>
            <Panel
              title="Document context"
              description="Retrieval and publisher maturity describe this document, not enterprise authority."
            >
              <div className="tag-row">
                <Status value={data.source.status} />
                <Badge variant="outline">
                  {data.source.item.format.toUpperCase()}
                </Badge>
                <Status value={data.source.publication.publication_status} />
              </div>
              <dl className="metadata">
                <div>
                  <dt>Publisher</dt>
                  <dd>
                    {String(data.source.item.metadata.publisher ?? "Unknown")}
                  </dd>
                </div>
                <div>
                  <dt>Publisher version</dt>
                  <dd>
                    {data.source.publication.publisher_version ?? "Unknown"}
                  </dd>
                </div>
                <div>
                  <dt>Last checked</dt>
                  <dd>{date(data.source.checked_at)}</dd>
                </div>
                <div>
                  <dt>Location</dt>
                  <dd>{data.source.item.location}</dd>
                </div>
              </dl>
              {data.source.error && (
                <p className="callout">{data.source.error}</p>
              )}
              <Button variant="outline" size="sm" asChild>
                <a href={`/knowledge?source_id=${id}&level=`}>
                  Filter knowledge by this source
                  <ArrowUpRight />
                </a>
              </Button>
              <details className="curation-guide">
                <summary>Source curation checks</summary>
                <ol className="option-list">
                  <li>
                    Confirm the publisher, edition and maturity of each evidence
                    document.
                  </li>
                  <li>
                    Open an interpretation; compare its ontology definition and
                    exact passage with the intended organizational scope.
                  </li>
                  <li>
                    Validate recorded relationships, then resolve proposals and
                    scoped adoption separately.
                  </li>
                </ol>
                <p className="table-note">
                  Source selection is managed in operator configuration. These
                  checks are guidance, not a saved curation or approval status.
                </p>
              </details>
              <Button variant="outline" size="sm" asChild>
                <a href={`/data?table=sources&record=${data.source.item.id}`}>
                  Inspect stored source record
                  <ArrowUpRight />
                </a>
              </Button>
            </Panel>
            <Panel
              title="Extracted interpretations"
              description="Each row is a version actually evidenced in this source, including historical revisions. Drill down to its original passages."
            >
              {!data.items.length ? (
                <Empty
                  title="No extracted interpretations"
                  description="Metadata-only, unprocessed or unsupported sources may have no extracted interpretations. Nothing has been inferred to fill this view."
                />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="statement-column">
                        Assertion
                      </TableHead>
                      <TableHead>Ontology concept</TableHead>
                      <TableHead>Version</TableHead>
                      <TableHead>Evidence</TableHead>
                      <TableHead>Explore</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.items.map((item) => (
                      <TableRow key={item.version.id}>
                        <TableCell>
                          <a
                            className="statement-link"
                            href={`/concepts/${item.concept_id}?version_id=${item.version.id}&source_id=${id}&level=${item.level ?? ""}`}
                          >
                            {assertionText(item.version, id)}
                          </a>
                          <span className="cell-caption">
                            {levelLabel(item.level)}
                          </span>
                        </TableCell>
                        <TableCell>
                          <OntologyBadge
                            name={item.version.candidate.semantic_type}
                          />
                        </TableCell>
                        <TableCell>
                          {semanticVersion(item.version.semantic_version)}
                          <span className="cell-caption">
                            Publisher:{" "}
                            {item.version.publication.publisher_version ??
                              "Unknown"}
                          </span>
                        </TableCell>
                        <TableCell>{item.evidence_count} passages</TableCell>
                        <TableCell>
                          <Button variant="outline" size="sm" asChild>
                            <a
                              href={`/explore?concept=${item.concept_id}`}
                              aria-label={`Explore relationships for ${item.version.candidate.statement}`}
                            >
                              <Network />
                              Relationships
                            </a>
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
              <CursorPager
                page={cursors.length}
                count={data.items.length}
                hasNext={data.next_cursor !== null}
                onPrevious={() => setCursors(cursors.slice(0, -1))}
                onNext={() => {
                  if (data.next_cursor)
                    setCursors([...cursors, data.next_cursor]);
                }}
              />
            </Panel>
          </>
        )}
      </LoadState>
    </>
  );
}
