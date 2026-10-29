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
import type { SourceGroup, SourceInspection } from "@/types";

export function Sources() {
  const resource = useResource<SourceGroup[]>("/api/source-groups");
  const [query, setQuery] = useState("");
  const [format, setFormat] = useState("");
  const [selected, setSelected] = useState<SourceGroup | null>(null);
  const overview = selected?.documents[0];
  const items = (resource.data ?? []).filter(
    (source) =>
      (!format || source.documents.some((doc) => doc.item.format === format)) &&
      `${source.name} ${source.publisher ?? ""} ${source.edition ?? ""} ${source.documents.map((doc) => `${doc.item.name} ${doc.item.location}`).join(" ")}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const { page, setPage, rows } = usePage(items);
  return (
    <>
      <Heading
        eyebrow="SOURCE REGISTRY"
        title="Know where it came from."
        description="One source per benchmark edition. Its pages, workbooks, mappings and policies remain individual evidence documents."
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
                  (resource.data ?? []).flatMap((source) =>
                    source.documents.map((doc) => doc.item.format),
                  ),
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
                  <TableHead>Documents</TableHead>
                  <TableHead>Retrieval state</TableHead>
                  <TableHead>Reference document maturity</TableHead>
                  <TableHead>
                    <span className="sr-only">Details</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((source) => (
                  <TableRow key={source.id}>
                    <TableCell>
                      <a
                        className="statement-link"
                        href={`/sources/${source.id}`}
                      >
                        {source.name}
                      </a>
                      <span className="cell-caption truncate-location">
                        {source.publisher ?? "Unknown publisher"}
                        {source.edition ? ` · ${source.edition}` : ""}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">
                        {source.documents.length}{" "}
                        {source.documents.length === 1
                          ? "document"
                          : "documents"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span>
                        {
                          source.documents.filter(
                            (doc) => doc.status === "available",
                          ).length
                        }{" "}
                        / {source.documents.length} available
                      </span>
                      {source.documents.some(
                        (doc) => doc.status === "error",
                      ) && (
                        <span className="cell-caption">
                          Retrieval errors in documents
                        </span>
                      )}
                      {source.documents.every(
                        (doc) => doc.item.metadata.metadata_only === true,
                      ) && <span className="cell-caption">Metadata only</span>}
                    </TableCell>
                    <TableCell>
                      <Status
                        value={
                          source.documents[0].publication.publication_status
                        }
                      />
                    </TableCell>
                    <TableCell>
                      <SheetTrigger asChild>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setSelected(source)}
                          aria-label={`Inspect source ${source.name}`}
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
                  Metadata describes the overview or first retained document,
                  not every member or enterprise adoption.
                </SheetDescription>
              </SheetHeader>
              {selected && overview && (
                <div className="sheet-body">
                  <h2>{selected.name}</h2>
                  <Button asChild>
                    <a href={`/sources/${selected.id}`}>
                      Browse extracted things
                      <ArrowUpRight />
                    </a>
                  </Button>
                  <div className="tag-row">
                    <Badge variant="outline">
                      {selected.documents.length} evidence documents
                    </Badge>
                  </div>
                  <dl className="metadata">
                    <div>
                      <dt>Publisher</dt>
                      <dd>{selected.publisher ?? "Unknown"}</dd>
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
                        {semanticVersion(overview.publication.semantic_version)}
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
  const documents = usePage(data?.group?.documents ?? [], 10);
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
        title={data?.group?.name ?? data?.source.item.name ?? "Source detail"}
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
              title={
                data.group ? "Reference document context" : "Document context"
              }
              description="Retrieval and publisher maturity describe this document, not every member or enterprise authority."
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
                <a href={`/knowledge?source_id=${id}&level=`}>
                  Filter knowledge by this source
                  <ArrowUpRight />
                </a>
              </Button>
              <Button variant="outline" size="sm" asChild>
                <a href={`/data?table=sources&record=${data.source.item.id}`}>
                  Inspect stored source record
                  <ArrowUpRight />
                </a>
              </Button>
            </Panel>
            {data.group && (
              <Panel
                title="Evidence documents"
                description={`${data.group.documents.length} original documents belong to this edition. Open a document for its own retrieval state, extracted versions and exact evidence.`}
              >
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Document</TableHead>
                      <TableHead>Role</TableHead>
                      <TableHead>Format</TableHead>
                      <TableHead>Retrieval</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {documents.rows.map((document) => {
                      const asset = document.item.metadata.benchmark_asset as
                        { role?: string } | undefined;
                      return (
                        <TableRow key={document.item.id}>
                          <TableCell>
                            <a
                              className="statement-link"
                              href={`/sources/${document.item.id}`}
                            >
                              {document.item.name}
                            </a>
                          </TableCell>
                          <TableCell>
                            {label(asset?.role ?? "document")}
                          </TableCell>
                          <TableCell>
                            {document.item.format.toUpperCase()}
                          </TableCell>
                          <TableCell>
                            <Status value={document.status} />
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
                <Pager
                  page={documents.page}
                  count={data.group.documents.length}
                  size={10}
                  onChange={documents.setPage}
                />
              </Panel>
            )}
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
                        Assertion
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
                            href={`/concepts/${item.concept_id}?version_id=${item.version.id}&source_id=${id}&level=${item.level ?? ""}`}
                          >
                            {assertionText(
                              item.version,
                              data.group
                                ? data.group.documents.map((doc) => doc.item.id)
                                : id,
                            )}
                          </a>
                          <span className="cell-caption">
                            {levelLabel(item.level)}
                          </span>
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
