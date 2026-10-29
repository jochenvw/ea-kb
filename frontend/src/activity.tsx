import { ArrowUpRight, RefreshCw } from "lucide-react";
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
  Heading,
  LoadState,
  Metric,
  Panel,
  Status,
  Technical,
  Empty,
} from "@/components/workspace";
import { date, label, useResource } from "@/lib/api";
import type { Pipeline, Scan, Statistics } from "@/types";

export function Activity({ scanRunning }: { scanRunning: boolean }) {
  const runId = new URLSearchParams(location.search).get("run_id");
  const pipeline = useResource<Pipeline | null>(
    `/api/pipeline/status${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`,
  );
  const scans = useResource<Scan[]>("/api/scans");
  const latest = scans.data?.[0];
  return (
    <>
      <Heading
        eyebrow="PIPELINE ACTIVITY"
        title="Every phase, accounted for."
        description="Durable execution records for indexing, enrichment and issue detection. Unchanged work can be safely skipped."
      >
        <Button
          variant="outline"
          onClick={() => {
            pipeline.reload();
            scans.reload();
          }}
        >
          <RefreshCw />
          Refresh activity
        </Button>
      </Heading>
      <div className="metrics-grid">
        <Metric
          title="Latest scan discovered"
          value={latest?.discovered}
          detail="Sources considered by the latest scan"
        />
        <Metric
          title="Processed"
          value={latest?.processed}
          detail="Changed or previously unprocessed inputs"
        />
        <Metric
          title="Skipped"
          value={latest?.skipped}
          detail="Reused from persisted state"
        />
        <Metric
          title="Failed"
          value={latest?.failed}
          detail="Failures recorded by the latest scan"
        />
      </div>
      <Panel
        title={runId ? "Selected pipeline run" : "Latest pipeline run"}
        description="Check records belong to this run; scan counts above are a separate ingestion history."
      >
        <LoadState resource={pipeline}>
          {pipeline.data ? (
            <>
              <div className="run-summary">
                <Status value={pipeline.data.run.status} />
                <span>Started {date(pipeline.data.run.started_at)}</span>
                <span>Finished {date(pipeline.data.run.finished_at)}</span>
              </div>
              <div className="stage-cards">
                {["indexing", "enrichment", "issues"].map((stage, index) => {
                  const record = pipeline.data!.executions.find(
                    (entry) =>
                      entry.kind === "stage" && entry.processor_id === stage,
                  );
                  const checks = pipeline.data!.executions.filter(
                    (entry) =>
                      entry.kind === "check" &&
                      entry.processor_id.startsWith(stage + "/"),
                  );
                  return (
                    <div key={stage}>
                      <span className="eyebrow">PHASE {index + 1}</span>
                      <h3>{label(stage)}</h3>
                      <Status value={record?.status ?? "not_run"} />
                      <p>
                        {checks.length} check records ·{" "}
                        {
                          checks.filter((entry) => entry.status === "skipped")
                            .length
                        }{" "}
                        skipped
                      </p>
                    </div>
                  );
                })}
              </div>
              <details className="technical">
                <summary>Check-level execution details</summary>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Processor</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Attempts</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pipeline.data.executions
                      .filter((item) => item.kind === "check")
                      .map((item) => (
                        <TableRow key={item.id}>
                          <TableCell>
                            {item.processor_id}
                            {item.error && (
                              <span className="cell-caption">{item.error}</span>
                            )}
                          </TableCell>
                          <TableCell>
                            <Status value={item.status} />
                          </TableCell>
                          <TableCell>{item.attempts}</TableCell>
                        </TableRow>
                      ))}
                  </TableBody>
                </Table>
              </details>
              <Technical
                value={pipeline.data.run}
                title="Run identifiers & configuration"
              />
            </>
          ) : (
            <Empty
              title="No pipeline run found"
              description="Run ekb pipeline run to execute indexing, enrichment and issues."
            />
          )}
        </LoadState>
      </Panel>
      <Panel
        title="Ingestion history"
        description="Recent source scans. These are not, on their own, a full three-phase run."
      >
        <LoadState resource={scans} empty={scans.data?.length === 0}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Started</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Discovered</TableHead>
                <TableHead>Processed</TableHead>
                <TableHead>Skipped</TableHead>
                <TableHead>Failed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {scans.data?.map((scan) => (
                <TableRow key={scan.id}>
                  <TableCell>{date(scan.started_at)}</TableCell>
                  <TableCell>
                    <Status value={scan.status} />
                  </TableCell>
                  <TableCell>{scan.discovered}</TableCell>
                  <TableCell>{scan.processed}</TableCell>
                  <TableCell>{scan.skipped}</TableCell>
                  <TableCell>{scan.failed}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </LoadState>
      </Panel>
      <ProcessingUsage />
      <Panel
        title="Run ingestion"
        description="This starts source indexing only. Use ekb pipeline run in the CLI for all three phases."
      >
        <form method="post" action="/scans" className="scan-form">
          <Button type="submit" disabled={scanRunning}>
            {scanRunning ? "Scan is running" : "Scan configured local sources"}
          </Button>
          <Button
            variant="outline"
            type="submit"
            name="public_manifest"
            value="true"
            disabled={scanRunning}
          >
            Scan configured public manifest
          </Button>
        </form>
        <p className="table-note">
          Uses server-configured paths and manifests. Browser requests cannot
          specify arbitrary filesystem paths.
        </p>
      </Panel>
    </>
  );
}

function ProcessingUsage() {
  const resource = useResource<Statistics>("/api/statistics");
  return (
    <Panel
      title="Processing usage"
      description="Reported usage for up to the latest 1,000 processing tasks."
      action={
        <Button variant="ghost" size="sm" asChild>
          <a href="/api/tasks" target="_blank" rel="noreferrer">
            Raw task records
            <ArrowUpRight />
          </a>
        </Button>
      }
    >
      <LoadState resource={resource}>
        {resource.data && (
          <>
            <dl className="metadata">
              <div>
                <dt>Input / output tokens</dt>
                <dd>
                  {resource.data.input_tokens.toLocaleString()} /{" "}
                  {resource.data.output_tokens.toLocaleString()}
                </dd>
              </div>
              <div>
                <dt>Cumulative model latency</dt>
                <dd>
                  {Math.round(resource.data.model_latency_ms).toLocaleString()}{" "}
                  ms
                </dd>
              </div>
              <div>
                <dt>Reported priced cost</dt>
                <dd>
                  {resource.data.configured_cost === null
                    ? "Unknown"
                    : resource.data.configured_cost.toLocaleString()}
                </dd>
              </div>
              <div>
                <dt>Validation failures</dt>
                <dd>{resource.data.validation_failures}</dd>
              </div>
              <div>
                <dt>Failed / cancelled tasks</dt>
                <dd>{resource.data.failures}</dd>
              </div>
            </dl>
            <p className="table-note">
              Cost sums only priced tasks in configured price units, not an
              invoice. Missing prices are not zero and totals may be incomplete.
            </p>
          </>
        )}
      </LoadState>
    </Panel>
  );
}
