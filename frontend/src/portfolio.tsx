import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import {
  Empty,
  Filter,
  Heading,
  LoadState,
  Metric,
  Panel,
} from "@/components/workspace";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { label, useResource } from "@/lib/api";
import type {
  BaselineSummary,
  ChangeImpactReport,
  ExceptionRiskReport,
} from "@/types";

function key(baseline: BaselineSummary) {
  return `${baseline.concept_id}/${baseline.version_id}`;
}

function baselineLabel(baseline: BaselineSummary) {
  return `${baseline.source} v${baseline.policy_version ?? "unknown"}${baseline.approved ? "" : " (not approved)"}`;
}

export function Portfolio() {
  const baselines = useResource<BaselineSummary[]>("/api/baselines");
  const [selected, setSelected] = useState("");
  const [previous, setPrevious] = useState("");
  const options = baselines.data ?? [];
  const current =
    options.find((item) => key(item) === selected) ?? options.at(-1);
  const old = options.find((item) => key(item) === previous);
  return (
    <>
      <Heading
        eyebrow="DELIVERY / PORTFOLIO"
        title="See exception risk and change impact across solutions."
        description="Read-only projections over the latest registered revision of every solution. Rates always show their applicable population; nothing here approves or files work."
      />
      <Panel
        title="Baseline"
        description="Registered with `ekb baselines register`. A baseline that is not approved keeps every finding unknown."
      >
        <LoadState resource={baselines} empty={options.length === 0}>
          {current ? (
            <div className="tag-row">
              <Filter
                name="Baseline"
                value={key(current)}
                onChange={setSelected}
                options={options.map((item) => [
                  key(item),
                  baselineLabel(item),
                ])}
              />
              <Badge variant={current.approved ? "default" : "outline"}>
                {current.approved ? "Approved" : "Awaiting human approval"}
              </Badge>
            </div>
          ) : (
            <Empty
              title="No baselines registered"
              description="Register one with `ekb baselines register BASELINE.json`."
            />
          )}
          {current && <p className="table-note">{current.statement}</p>}
        </LoadState>
      </Panel>
      {current && <ExceptionRisk baseline={current} />}
      {current && (
        <Panel
          title="Change impact"
          description="Compare an earlier baseline version with the selected one to draft owner-directed work."
        >
          <Filter
            name="Compare with"
            value={previous}
            onChange={setPrevious}
            options={[
              ["", "Select an earlier baseline"],
              ...options
                .filter((item) => key(item) !== key(current))
                .map((item): [string, string] => [
                  key(item),
                  baselineLabel(item),
                ]),
            ]}
          />
          {old ? (
            <ChangeImpact old={old} next={current} />
          ) : (
            <p className="muted">
              Register a second baseline version to compare.
            </p>
          )}
        </Panel>
      )}
    </>
  );
}

function ExceptionRisk({ baseline }: { baseline: BaselineSummary }) {
  const report = useResource<ExceptionRiskReport>(
    "/api/portfolio/exception-risk",
    {
      baseline_concept_id: baseline.concept_id,
      baseline_version_id: baseline.version_id,
    },
  );
  const data = report.data;
  return (
    <Panel
      title="Exception risk"
      description={data?.population ?? "Applicable population per requirement."}
    >
      <LoadState resource={report}>
        {data && (
          <>
            <div className="metrics-grid">
              <Metric
                title="Solutions"
                value={data.solutions}
                detail={`${data.solutions_without_design} without a structured design`}
              />
              <Metric
                title="Exception decisions"
                value={data.distinct_exception_decisions}
                detail={`${data.solutions_with_exceptions} solutions with exceptions`}
              />
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Requirement</TableHead>
                  <TableHead>Applicable</TableHead>
                  <TableHead>Active exceptions</TableHead>
                  <TableHead>Rate</TableHead>
                  <TableHead>Violated</TableHead>
                  <TableHead>Unknown applicability</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.requirements.map((item) => (
                  <TableRow key={item.requirement.version_id}>
                    <TableCell>
                      <a
                        className="statement-link"
                        href={`/concepts/${item.requirement.concept_id}`}
                      >
                        {item.requirement.statement}
                      </a>
                    </TableCell>
                    <TableCell>{item.applicable}</TableCell>
                    <TableCell>{item.active_exceptions}</TableCell>
                    <TableCell>
                      {item.rate === null
                        ? "No applicable population"
                        : `${Math.round(item.rate * 100)}% (${item.active_exceptions} of ${item.applicable})`}
                    </TableCell>
                    <TableCell>{item.violated}</TableCell>
                    <TableCell>{item.applicability_unknown}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {[
              ...data.signals.map(
                (item) => `${label(item.kind)}: ${item.reason}`,
              ),
              ...data.gaps,
              ...data.limitations,
            ].map((text) => (
              <p className="table-note" key={text}>
                {text}
              </p>
            ))}
          </>
        )}
      </LoadState>
    </Panel>
  );
}

function ChangeImpact({
  old,
  next,
}: {
  old: BaselineSummary;
  next: BaselineSummary;
}) {
  const report = useResource<ChangeImpactReport>(
    "/api/portfolio/change-impact",
    {
      old_concept_id: old.concept_id,
      old_version_id: old.version_id,
      new_concept_id: next.concept_id,
      new_version_id: next.version_id,
    },
  );
  const data = report.data;
  return (
    <LoadState resource={report}>
      {data && (
        <>
          <p className="table-note">
            {label(data.stage)} change from {data.old_policy} to{" "}
            {data.new_policy} · {data.changes.length} requirement changes ·{" "}
            {data.items.length} affected solution items
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Solution</TableHead>
                <TableHead>Change</TableHead>
                <TableHead>Certainty</TableHead>
                <TableHead>Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((item) => (
                <TableRow key={item.id}>
                  <TableCell>
                    {item.solution} v{item.version}
                    <small className="cell-caption">
                      {item.owner ?? "Owner not recorded"}
                    </small>
                  </TableCell>
                  <TableCell>
                    {label(item.change.kind)}: {item.change.statement}
                  </TableCell>
                  <TableCell>{label(item.certainty)}</TableCell>
                  <TableCell>
                    {item.action}
                    <small className="cell-caption">{item.reason}</small>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {data.drafts.map((draft) => (
            <details className="technical" key={draft.route + draft.title}>
              <summary>
                Draft for {draft.route}: {draft.title}
              </summary>
              <pre>{draft.body}</pre>
            </details>
          ))}
        </>
      )}
    </LoadState>
  );
}
