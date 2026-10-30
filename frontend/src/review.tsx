import { useState } from "react";
import { ArrowUpRight, LockKeyhole } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  ScopeInfo,
  Status,
  Technical,
  usePage,
  Empty,
  CursorPager,
} from "@/components/workspace";
import { RelatedConcept } from "@/knowledge";
import { date, externalUrl, label, useResource } from "@/lib/api";
import type { BacklogPage, Decision, Finding, Issue } from "@/types";

export function Review({ governance }: { governance: boolean }) {
  const query = new URLSearchParams(location.search);
  return (
    <>
      <Heading
        eyebrow="DECIDE / SCOPED APPLICABILITY"
        title="Reconcile before adopting."
        description="Resolve duplicate interpretations and conflicting requirements. Confirm evidence and scope before recording an explicit, auditable decision."
      >
        <Button variant="outline" asChild>
          <a href="/reconciliation">
            Reconciliation log
            <ArrowUpRight />
          </a>
        </Button>
      </Heading>
      <details className="curation-guide">
        <summary>Before recording a decision</summary>
        <p>
          Verify the exact source passage, the{" "}
          <a href="/ontology">ontology definition</a>, organizational scope and
          effective period. A published document or a recorded mapping does not
          establish enterprise authority; adoption remains a separate scoped
          decision.
        </p>
      </details>
      {!governance && (
        <div className="read-only">
          <LockKeyhole size={18} />
          <div>
            <strong>Read-only workspace</strong>
            <p>
              Browse all evidence and history. HTTP governance must be
              configured by an operator before decisions can be submitted.
            </p>
          </div>
        </div>
      )}
      <Tabs
        defaultValue={
          query.has("issue_id") ||
          query.has("status") ||
          query.get("view") === "issues"
            ? "issues"
            : "proposals"
        }
        className="review-tabs"
      >
        <TabsList aria-label="Review views">
          <TabsTrigger value="issues">Issues</TabsTrigger>
          <TabsTrigger value="proposals">Reconciliation backlog</TabsTrigger>
        </TabsList>
        <TabsContent value="issues">
          <Issues governance={governance} />
        </TabsContent>
        <TabsContent value="proposals">
          <Proposals governance={governance} backlog />
        </TabsContent>
      </Tabs>
    </>
  );
}

function Issues({ governance }: { governance: boolean }) {
  const query = new URLSearchParams(location.search);
  const [status, setStatus] = useState(query.get("status") ?? "open");
  const [severity, setSeverity] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(
    query.get("issue_id"),
  );
  const parameters = new URLSearchParams({ limit: "1000" });
  if (status) parameters.set("status", status);
  if (severity) parameters.set("severity", severity);
  const resource = useResource<Issue[]>(`/api/issues?${parameters}`);
  const items = (resource.data ?? []).filter((item) =>
    `${item.explanation} ${label(item.issue_type)} ${item.owner ?? ""}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  items.sort(
    (a, b) =>
      ["critical", "high", "medium", "low"].indexOf(a.severity) -
      ["critical", "high", "medium", "low"].indexOf(b.severity),
  );
  const { page, setPage, rows } = usePage(items);
  return (
    <Panel
      title="Issue queue"
      description="Persistent exceptions, ordered by severity. Resolve the cause; do not mistake a flag for a compliance verdict."
    >
      <div className="toolbar">
        <SearchBox
          value={search}
          name="Search loaded issues"
          placeholder="Search loaded issues…"
          onChange={(value) => {
            setSearch(value);
            setPage(0);
          }}
        />
        <Filter
          name="Issue status"
          value={status}
          onChange={(value) => {
            setStatus(value);
            setPage(0);
          }}
          options={[
            ["", "All statuses"],
            ...[
              "open",
              "under_review",
              "reopened",
              "resolved",
              "dismissed",
              "accepted_risk",
            ].map((value): [string, string] => [value, label(value)]),
          ]}
        />
        <Filter
          name="Issue severity"
          value={severity}
          onChange={(value) => {
            setSeverity(value);
            setPage(0);
          }}
          options={[
            ["", "All severities"],
            ...["critical", "high", "medium", "low"].map(
              (value): [string, string] => [value, label(value)],
            ),
          ]}
        />
      </div>
      <p className="table-note">
        Status and severity filter the server. Search and pages apply to up to
        1,000 loaded matches.
      </p>
      <Sheet
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      >
        <LoadState resource={resource} empty={items.length === 0}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Severity</TableHead>
                <TableHead className="statement-column">Issue</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead>
                  <span className="sr-only">Inspect</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((issue) => (
                <TableRow key={issue.id}>
                  <TableCell>
                    <Status value={issue.severity} />
                  </TableCell>
                  <TableCell>
                    <strong>{label(issue.issue_type)}</strong>
                    <span className="cell-caption line-clamp-2">
                      {issue.explanation}
                    </span>
                    {!issue.observed && (
                      <span className="cell-caption">
                        No longer observed; review status retained
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Status value={issue.status} />
                  </TableCell>
                  <TableCell>
                    {Object.values(issue.scope).filter(Boolean).join(" · ") ||
                      "Unknown"}
                  </TableCell>
                  <TableCell>
                    <SheetTrigger asChild>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setSelected(issue.id)}
                        aria-label={`Inspect issue ${label(issue.issue_type)}`}
                      >
                        Inspect
                      </Button>
                    </SheetTrigger>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Pager page={page} count={items.length} onChange={setPage} />
        </LoadState>
        <SheetContent className="detail-sheet">
          <SheetHeader>
            <SheetTitle>Issue details</SheetTitle>
            <SheetDescription>
              Review the evidence, then record a reasoned action.
            </SheetDescription>
          </SheetHeader>
          {selected && <IssueDetails id={selected} governance={governance} />}
        </SheetContent>
      </Sheet>
    </Panel>
  );
}

function IssueDetails({ id, governance }: { id: string; governance: boolean }) {
  const resource = useResource<{
    issue: Issue;
    events: {
      action: string;
      actor: string;
      rationale: string;
      recorded_at: string;
    }[];
  }>(`/api/issues/${id}`);
  const issue = resource.data?.issue;
  return (
    <div className="sheet-body">
      <LoadState resource={resource}>
        {issue && (
          <>
            <div className="tag-row">
              <Status value={issue.severity} />
              <Status value={issue.status} />
            </div>
            <h2>{label(issue.issue_type)}</h2>
            <p>{issue.explanation}</p>
            <dl className="metadata">
              <div>
                <dt>Confidence</dt>
                <dd>{Math.round(issue.confidence * 100)}% check confidence</dd>
              </div>
              <div>
                <dt>Owner</dt>
                <dd>{issue.owner ?? "Unassigned"}</dd>
              </div>
              <div>
                <dt>Last observed</dt>
                <dd>{date(issue.last_observed)}</dd>
              </div>
              <div>
                <dt>Currently observed</dt>
                <dd>
                  {issue.observed ? "Yes" : "No — review state is retained"}
                </dd>
              </div>
            </dl>
            <h3>Applicability scope</h3>
            <ScopeInfo scope={issue.scope} />
            <h3>Supporting findings</h3>
            {issue.finding_ids.length ? (
              issue.finding_ids.map((finding, index) => (
                <Button key={finding} variant="outline" asChild>
                  <a href={`/review?finding_id=${finding}`}>
                    Inspect supporting finding {index + 1}
                    <ArrowUpRight />
                  </a>
                </Button>
              ))
            ) : (
              <p className="muted">No supporting findings linked.</p>
            )}
            <ConceptReferences ids={issue.concept_ids} />
            {issue.options.length > 0 && (
              <>
                <h3>Suggested review options</h3>
                <ul className="option-list">
                  {issue.options.map((option) => (
                    <li key={option}>{option}</li>
                  ))}
                </ul>
              </>
            )}
            {governance && (
              <form
                method="post"
                action="/review/issues"
                className="governance-form"
              >
                <h3>Record issue review</h3>
                <input type="hidden" name="issue_id" value={id} />
                <Label htmlFor="issue-review-status">New review status</Label>
                <select
                  className="control"
                  id="issue-review-status"
                  name="status"
                  defaultValue="under_review"
                >
                  {[
                    "under_review",
                    "resolved",
                    "dismissed",
                    "accepted_risk",
                    "open",
                  ].map((value) => (
                    <option key={value} value={value}>
                      {label(value)}
                    </option>
                  ))}
                </select>
                <Label htmlFor="issue-rationale">Rationale</Label>
                <Textarea
                  name="rationale"
                  id="issue-rationale"
                  required
                  placeholder="Explain the evidence and reason for this action…"
                />
                <Button type="submit">Record issue review</Button>
                <p className="table-note">
                  Requires operator authentication. The change is appended to
                  the audit trail.
                </p>
              </form>
            )}
            <details className="technical">
              <summary>
                Issue history ({resource.data?.events.length ?? 0})
              </summary>
              {resource.data?.events.map((event, index) => (
                <div key={index} className="record-block">
                  <strong>{label(event.action)}</strong>
                  <p>{event.rationale}</p>
                  <small>
                    {event.actor} · {date(event.recorded_at)}
                  </small>
                </div>
              ))}
            </details>
            <Technical
              value={issue}
              title="Issue identifiers & check metadata"
            />
          </>
        )}
      </LoadState>
    </div>
  );
}

export function Proposals({
  governance,
  backlog = false,
}: {
  governance: boolean;
  backlog?: boolean;
}) {
  const [status, setStatus] = useState("all");
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [selected, setSelected] = useState<string | null>(
    new URLSearchParams(location.search).get("finding_id"),
  );
  const cursor = cursors.at(-1);
  const suffix = `limit=50${cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`;
  const ledger = useResource<Finding[]>(
    backlog ? null : `/api/reconciliation/findings?${suffix}`,
  );
  const queue = useResource<BacklogPage>(
    backlog ? `/api/reconciliation/backlog?${suffix}&status=${status}` : null,
  );
  const resource = backlog
    ? { ...queue, data: queue.data?.items.map((item) => item.finding) ?? null }
    : ledger;
  return (
    <Panel
      title={backlog ? "Active reconciliation backlog" : "Recorded proposals"}
      description={
        backlog
          ? "Current enrichment and manual proposals awaiting a decision, deferred or reversed. Accepted, rejected and superseded-run proposals are excluded."
          : "A paged evidence ledger, not a list of approved policies. Historical proposals are retained."
      }
    >
      {backlog && (
        <div className="toolbar">
          <Filter
            name="Backlog decision state"
            value={status}
            onChange={(value) => {
              setStatus(value);
              setCursors([null]);
            }}
            options={[
              ["all", "All outstanding"],
              ["pending", "No decision"],
              ["deferred", "Deferred"],
              ["reversed", "Reversed"],
            ]}
          />
          <span className="muted">
            {queue.data?.total ?? "…"} outstanding proposals
          </span>
        </div>
      )}
      <Sheet
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      >
        <LoadState resource={resource} empty={resource.data?.length === 0}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="statement-column">Proposal</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Confidence</TableHead>
                {backlog && <TableHead>Decision state</TableHead>}
                <TableHead>
                  <span className="sr-only">Inspect</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {resource.data?.map((finding) => (
                <TableRow key={finding.id}>
                  <TableCell>
                    <strong>{label(finding.check_id)}</strong>
                    <span className="cell-caption line-clamp-2">
                      {finding.explanation}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">
                      {label(finding.relationship ?? finding.action)}
                    </Badge>
                  </TableCell>
                  <TableCell>{Math.round(finding.confidence * 100)}%</TableCell>
                  {backlog && (
                    <TableCell>
                      <Status
                        value={
                          queue.data?.items.find(
                            (item) => item.finding.id === finding.id,
                          )?.decision?.outcome ?? "pending"
                        }
                      />
                    </TableCell>
                  )}
                  <TableCell>
                    <SheetTrigger asChild>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setSelected(finding.id)}
                        aria-label={`Inspect proposal ${finding.explanation}`}
                      >
                        Inspect
                      </Button>
                    </SheetTrigger>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </LoadState>
        <SheetContent className="detail-sheet">
          <SheetHeader>
            <SheetTitle>Proposal evidence</SheetTitle>
            <SheetDescription>
              Proposals do not change authority until an explicit governed
              decision.
            </SheetDescription>
          </SheetHeader>
          {selected && <FindingDetails id={selected} governance={governance} />}
        </SheetContent>
      </Sheet>
      <CursorPager
        page={cursors.length}
        count={resource.data?.length ?? 0}
        disabled={resource.loading || !!resource.error}
        hasNext={backlog ? queue.data?.next_cursor != null : undefined}
        onPrevious={() => setCursors(cursors.slice(0, -1))}
        onNext={() => {
          const last = resource.data?.at(-1)?.id;
          if (last) setCursors([...cursors, last]);
        }}
      />
    </Panel>
  );
}

function FindingDetails({
  id,
  governance,
}: {
  id: string;
  governance: boolean;
}) {
  const resource = useResource<Finding>(`/api/reconciliation/findings/${id}`);
  const history = useResource<Decision[]>(
    `/api/reconciliation/decisions?finding_id=${id}`,
  );
  const finding = resource.data;
  const head = [...(history.data ?? [])]
    .sort(
      (a, b) =>
        a.recorded_at.localeCompare(b.recorded_at) || a.id.localeCompare(b.id),
    )
    .at(-1);
  return (
    <div className="sheet-body">
      <LoadState resource={resource}>
        {finding && (
          <>
            <div className="tag-row">
              <Badge variant="secondary">
                {label(finding.relationship ?? finding.action)}
              </Badge>
              <Badge variant="outline">
                {Math.round(finding.confidence * 100)}% check confidence
              </Badge>
            </div>
            <h2>{label(finding.check_id)}</h2>
            <p>{finding.explanation}</p>
            {finding.uncertainty && (
              <p className="callout">{finding.uncertainty}</p>
            )}
            <h3>Proposed applicability</h3>
            <ScopeInfo scope={finding.scope} />
            <ConceptReferences ids={finding.concept_ids} />
            <h3>Published passages</h3>
            {finding.passages.length ? (
              finding.passages.map((passage, index) => (
                <article className="evidence" key={index}>
                  <h4>{passage.section}</h4>
                  <blockquote>{passage.excerpt}</blockquote>
                  <p className="table-note">
                    Exact offsets {passage.start}–{passage.end}
                  </p>
                  {externalUrl(passage.source_url) && (
                    <a
                      className="external-link"
                      href={externalUrl(passage.source_url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Publisher location
                      <ArrowUpRight size={14} />
                    </a>
                  )}
                  <Technical value={passage} title="Passage provenance" />
                </article>
              ))
            ) : (
              <p className="muted">
                No separate published passages. Inspect the linked concepts for
                occurrence evidence.
              </p>
            )}
            <h3>Latest decision</h3>
            <LoadState resource={history}>
              {head ? (
                <div className="record-block">
                  <Status value={head.outcome} />
                  <p>{head.rationale}</p>
                  <small>
                    {head.actor} · {date(head.recorded_at)}
                  </small>
                </div>
              ) : (
                <p className="muted">
                  No decision recorded. This remains a proposal.
                </p>
              )}
            </LoadState>
            {governance && (
              <form
                method="post"
                action="/review/decisions"
                className="governance-form"
              >
                <h3>Record a governed decision</h3>
                <fieldset disabled={history.loading || !!history.error}>
                  <input type="hidden" name="finding_id" value={id} />
                  <input
                    type="hidden"
                    name="previous_id"
                    value={head?.id ?? ""}
                  />
                  <Label htmlFor="decision-outcome">Decision</Label>
                  <select
                    className="control"
                    id="decision-outcome"
                    name="outcome"
                    defaultValue="deferred"
                  >
                    <option value="deferred">Defer for further evidence</option>
                    <option value="accepted">Accept proposal</option>
                    <option value="rejected">Reject proposal</option>
                    <option value="reversed" disabled={!head}>
                      Reverse previous decision
                    </option>
                  </select>
                  <Label htmlFor="decision-rationale">Rationale</Label>
                  <Textarea
                    required
                    id="decision-rationale"
                    name="rationale"
                    placeholder="Explain the scope, evidence and reason for this decision…"
                  />
                  <p className="table-note">
                    Acceptance can change identity, relationships, versions or
                    scoped canonical authority. Inspect all evidence first.
                  </p>
                  <Button type="submit">Record decision</Button>
                </fieldset>
                <p className="table-note">
                  Requires operator authentication. An append-only audit and
                  previous-decision guard protect the record.
                </p>
              </form>
            )}
            <Technical
              value={finding}
              title="Proposal identifiers & check metadata"
            />
          </>
        )}
      </LoadState>
    </div>
  );
}

function ConceptReferences({ ids }: { ids: string[] }) {
  return (
    ids.length > 0 && (
      <>
        <h3>Related interpretations</h3>
        {ids.slice(0, 10).map((id) => (
          <RelatedConcept id={id} key={id} />
        ))}
        {ids.length > 10 && (
          <p className="table-note">
            Showing the first 10 of {ids.length} links. All identifiers are in
            the technical details.
          </p>
        )}
      </>
    )
  );
}

export function History() {
  const resource = useResource<Decision[]>("/api/reconciliation/decisions");
  const items = [...(resource.data ?? [])].sort((a, b) =>
    b.recorded_at.localeCompare(a.recorded_at),
  );
  const { page, setPage, rows } = usePage(items);
  return (
    <Panel
      title="Decision history"
      description="Append-only human, rule or model decisions, newest first. An audit entry is not blanket applicability."
    >
      <LoadState resource={resource}>
        {!items.length ? (
          <Empty
            title="No decisions recorded"
            description="No proposal has been accepted, rejected, deferred or reversed in this workspace."
          />
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Decision</TableHead>
                  <TableHead>Rationale</TableHead>
                  <TableHead>Actor</TableHead>
                  <TableHead>Recorded</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((decision) => (
                  <TableRow key={decision.id}>
                    <TableCell>
                      <a href={`/review?finding_id=${decision.finding_id}`}>
                        <Status value={decision.outcome} />
                      </a>
                    </TableCell>
                    <TableCell>{decision.rationale}</TableCell>
                    <TableCell>{decision.actor}</TableCell>
                    <TableCell>{date(decision.recorded_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pager page={page} count={items.length} onChange={setPage} />
          </>
        )}
      </LoadState>
    </Panel>
  );
}
