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
  externalUrl,
  label,
  semanticVersion,
  useResource,
} from "@/lib/api";
import type { Source, SourceInspection } from "@/types";

export function Sources() {
  const resource = useResource<Source[]>("/api/sources");
  const [query, setQuery] = useState("");
  const [format, setFormat] = useState("");
  const [selected, setSelected] = useState<Source | null>(null);
  const items = (resource.data ?? []).filter(
    (source) =>
      (!format || source.item.format === format) &&
      `${source.item.name} ${source.item.location} ${String(source.item.metadata.publisher ?? "")}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const { page, setPage, rows } = usePage(items);
  return (
    <>
      <Heading
        eyebrow="SOURCE REGISTRY"
        title="Know where it came from."
        description="Inspect registered sources, retrieval state and publication metadata. Originals remain in the configured artifact store."
      />
      <Panel
        title="Registered sources"
        description="Includes historical, fixture and metadata-only records; not just the latest ingestion."
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
        <LoadState resource={resource} empty={items.length === 0}>
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
                  <TableHead>Publisher maturity</TableHead>
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
                        {source.item.location}
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
                  Retrieval and publication are distinct from enterprise
                  adoption.
                </SheetDescription>
              </SheetHeader>
              {selected && (
                <div className="sheet-body">
                  <h2>{selected.item.name}</h2>
                  <Button asChild>
                    <a href={`/sources/${selected.item.id}`}>
                      Browse extracted things
                      <ArrowUpRight />
                    </a>
                  </Button>
                  <div className="tag-row">
                    <Status value={selected.status} />
                    <Badge variant="outline">
                      {selected.item.format.toUpperCase()}
                    </Badge>
                  </div>
                  <dl className="metadata">
                    <div>
                      <dt>Publisher</dt>
                      <dd>
                        {String(selected.item.metadata.publisher ?? "Unknown")}
                      </dd>
                    </div>
                    <div>
                      <dt>Last checked</dt>
                      <dd>{date(selected.checked_at)}</dd>
                    </div>
                    <div>
                      <dt>Publisher maturity</dt>
                      <dd>{selected.publication.publication_status}</dd>
                    </div>
                    <div>
                      <dt>Publisher version</dt>
                      <dd>
                        {selected.publication.publisher_version ?? "Unknown"}
                      </dd>
                    </div>
                    <div>
                      <dt>Semantic version</dt>
                      <dd>
                        {semanticVersion(selected.publication.semantic_version)}
                      </dd>
                    </div>
                    <div>
                      <dt>Location</dt>
                      <dd>{selected.item.location}</dd>
                    </div>
                  </dl>
                  {selected.error && (
                    <p className="callout">{selected.error}</p>
                  )}
                  {externalUrl(selected.item.location) && (
                    <Button asChild variant="outline">
                      <a
                        href={externalUrl(selected.item.location)}
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
          <Pager page={page} count={items.length} onChange={setPage} />
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
        eyebrow="REFERENCE / SOURCE"
        title={data?.source.item.name ?? "Source detail"}
        description="Follow this source into its extracted interpretations, exact evidence and relationships."
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
              title="Source context"
              description="Retrieval and publisher maturity do not establish enterprise authority."
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
                <a href={`/data?table=sources&record=${id}`}>
                  Inspect stored source record
                  <ArrowUpRight />
                </a>
              </Button>
            </Panel>
            <Panel
              title="Extracted things"
              description="Each row is a version actually evidenced in this source, including historical revisions. Drill down to its original passages."
            >
              {!data.items.length ? (
                <Empty
                  title="No extracted interpretations"
                  description="Metadata-only, unprocessed or unsupported sources may have no extracted things. Nothing has been inferred to fill this view."
                />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="statement-column">
                        Interpretation
                      </TableHead>
                      <TableHead>Kind</TableHead>
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
                            href={`/concepts/${item.concept_id}?version_id=${item.version.id}`}
                          >
                            {item.version.candidate.statement}
                          </a>
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary">
                            {label(item.version.candidate.kind)}
                          </Badge>
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
