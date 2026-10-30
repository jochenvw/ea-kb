import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
  Filter,
  CursorPager,
  Technical,
  SearchBox,
} from "@/components/workspace";
import { date, label, useResource } from "@/lib/api";
import { History, Proposals } from "@/review";
import type { TableInfo, TablePage, LogPage } from "@/types";

export function ReconciliationLog() {
  return (
    <>
      <Heading
        eyebrow="REFERENCE / RECONCILIATION"
        title="Every proposal. Every decision."
        description="Inspect the reconciliation ledger without changing it. Pending work belongs in the backlog."
      >
        <Button asChild>
          <a href="/review">Open reconciliation backlog</a>
        </Button>
      </Heading>
      <Tabs defaultValue="proposals" className="review-tabs">
        <TabsList aria-label="Reconciliation log views">
          <TabsTrigger value="proposals">Proposal ledger</TabsTrigger>
          <TabsTrigger value="history">Decision history</TabsTrigger>
        </TabsList>
        <TabsContent value="proposals">
          <Proposals governance={false} />
        </TabsContent>
        <TabsContent value="history">
          <History />
        </TabsContent>
      </Tabs>
      <Panel
        title="Issue review audit"
        description="Inspect append-only issue events in the read-only data inspector."
      >
        <div className="heading-actions">
          <Button variant="outline" asChild>
            <a href="/data?table=issue_events">Issue review events</a>
          </Button>
        </div>
      </Panel>
    </>
  );
}

export function DataExplorer() {
  const query = new URLSearchParams(location.search);
  const name = query.get("table") ?? "sources";
  const record = query.get("record") ?? "";
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const catalog = useResource<TableInfo[]>("/api/inspection/tables");
  const cursor = cursors.at(-1);
  const resource = useResource<TablePage>(
    `/api/inspection/tables/${encodeURIComponent(name)}?limit=50${record ? `&record=${encodeURIComponent(record)}` : cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`,
  );
  const data = resource.data;
  return (
    <>
      <Heading
        eyebrow="OPERATIONS / READ-ONLY DATA"
        title="Inspect the stored records."
        description="Browse only EKB-owned tables. No SQL console, schema access, edits or deletes. Governance remains in the review workflow."
      />
      <Panel
        title="Data inspector"
        description="Stable primary-key pages, with foreign-key links. Nested JSON, long text and arrays are bounded previews, not full exports."
      >
        <LoadState resource={catalog}>
          <div className="toolbar">
            <Filter
              name="EKB table"
              value={name}
              onChange={(table) =>
                location.assign(`/data?table=${encodeURIComponent(table)}`)
              }
              options={(catalog.data ?? []).map((table) => [
                table.name,
                `${label(table.name)} (${table.count.toLocaleString()})`,
              ])}
            />
            <form action="/data" method="get" className="record-filter">
              <input type="hidden" name="table" value={name} />
              <Input
                name="record"
                aria-label="Record identifier"
                defaultValue={record}
                placeholder="Exact record identifier (optional)"
                maxLength={200}
              />
              <Button type="submit" variant="outline">
                Find record
              </Button>
            </form>
          </div>
        </LoadState>
        {record && (
          <p className="table-note">
            Showing one exact record.{" "}
            <a href={`/data?table=${encodeURIComponent(name)}`}>
              Clear record filter
            </a>
          </p>
        )}
        <LoadState resource={resource} empty={data?.rows.length === 0}>
          {data && (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    {data.columns.map((column) => (
                      <TableHead key={column.name}>
                        {label(column.name)}
                        <span className="cell-caption">{column.type}</span>
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.rows.map((row, index) => (
                    <TableRow key={index}>
                      {data.columns.map((column) => {
                        const value = row[column.name];
                        return (
                          <TableCell key={column.name} className="data-cell">
                            {value === null ? (
                              <span className="muted">Null</span>
                            ) : typeof value === "object" ? (
                              <Technical
                                value={value}
                                title="Inspect JSON preview"
                              />
                            ) : column.reference ? (
                              <a
                                href={`/data?table=${encodeURIComponent(column.reference)}&record=${encodeURIComponent(String(value))}`}
                              >
                                {String(value)}
                              </a>
                            ) : (
                              <span>{String(value)}</span>
                            )}
                          </TableCell>
                        );
                      })}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <CursorPager
                page={cursors.length}
                count={data.rows.length}
                hasNext={data.next_cursor !== null && !record}
                disabled={resource.loading || !!resource.error}
                onPrevious={() => setCursors(cursors.slice(0, -1))}
                onNext={() => {
                  if (data.next_cursor)
                    setCursors([...cursors, data.next_cursor]);
                }}
              />
            </>
          )}
        </LoadState>
      </Panel>
    </>
  );
}

export function Logs() {
  const resource = useResource<LogPage>("/api/inspection/logs?limit=100");
  const [query, setQuery] = useState("");
  const rows = (resource.data?.events ?? []).filter((event) =>
    `${event.event} ${event.process} ${JSON.stringify(event.fields)}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <>
      <Heading
        eyebrow="OPERATIONS / APPLICATION LOGS"
        title="See what happened."
        description="Recent structured application events across local processes. Debug payloads, exception messages and arbitrary files are never served."
      >
        <Button variant="outline" onClick={resource.reload}>
          <RefreshCw />
          Refresh logs
        </Button>
      </Heading>
      <Panel
        title="Recent operational events"
        description="Up to 100 events from the newest 16 log files, reading at most 64 KiB per file. Only allowlisted event names and diagnostic fields are shown."
      >
        <SearchBox
          name="Search loaded log events"
          placeholder="Filter loaded events, identifiers or error types…"
          value={query}
          onChange={setQuery}
        />
        {resource.data && (
          <p className="table-note">
            {resource.data.files_read} files read.{" "}
            {resource.data.truncated
              ? "A bounded recent tail is shown; older events remain on disk."
              : "All events within the inspected tails are shown."}
          </p>
        )}
        <LoadState resource={resource} empty={rows.length === 0}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Event</TableHead>
                <TableHead>Recorded</TableHead>
                <TableHead>Process</TableHead>
                <TableHead>Diagnostic fields</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((event, index) => (
                <TableRow key={index}>
                  <TableCell>
                    <strong>{label(event.event)}</strong>
                  </TableCell>
                  <TableCell>{date(event.timestamp)}</TableCell>
                  <TableCell>{event.process}</TableCell>
                  <TableCell>
                    <Technical
                      value={event.fields}
                      title="Inspect event fields"
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </LoadState>
      </Panel>
    </>
  );
}
