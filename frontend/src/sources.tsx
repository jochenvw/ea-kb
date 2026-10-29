import { useState } from "react";
import { ArrowUpRight } from "lucide-react";
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
} from "@/components/workspace";
import { date, externalUrl, semanticVersion, useResource } from "@/lib/api";
import type { Source } from "@/types";

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
                      <strong>{source.item.name}</strong>
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
