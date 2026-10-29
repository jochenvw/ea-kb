import { useState, type FormEvent } from "react";
import { ArrowRight, ArrowUpRight, GitCompareArrows } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  Status,
  Technical,
  Empty,
} from "@/components/workspace";
import { externalUrl, label, semanticVersion, useResource } from "@/lib/api";
import type { Comparison, Control } from "@/types";

export function Benchmarks() {
  const query = new URLSearchParams(location.search);
  const [reference, setReference] = useState(
    query.get("reference") ?? "mcsb-v2:NS-6",
  );
  const [selected, setSelected] = useState(reference);
  const [from, setFrom] = useState(query.get("from_edition") ?? "mcsb-v1");
  const [to, setTo] = useState(query.get("to_edition") ?? "mcsb-v2");
  const [comparison, setComparison] = useState<{
    from: string;
    to: string;
  } | null>(null);
  const resource = useResource<Control>(
    `/api/benchmarks/controls/${encodeURIComponent(selected)}`,
  );
  const changes = useResource<Comparison>(
    comparison
      ? `/api/benchmarks/compare?${new URLSearchParams({ from_edition: comparison.from, to_edition: comparison.to })}`
      : null,
  );
  const context = resource.data;
  const control = context?.control.control;
  function inspect(event: FormEvent) {
    event.preventDefault();
    setSelected(reference.trim());
  }
  const editions: [string, string][] = [
    ["asb-v2", "ASB v2"],
    ["mcsb-v1", "MCSB v1"],
    ["mcsb-v2", "MCSB v2"],
  ];
  return (
    <>
      <Heading
        eyebrow="BENCHMARK EXPLORER"
        title="Trace controls, not assumptions."
        description="Inspect published controls, their native mappings and proposed changes between editions. Browsing never invokes a model."
      />
      <Panel
        title="Inspect a benchmark control"
        description="Use a stable reference such as mcsb-v2:NS-6."
      >
        <form className="benchmark-form" onSubmit={inspect}>
          <Label htmlFor="control-reference">Control reference</Label>
          <Input
            id="control-reference"
            value={reference}
            onChange={(event) => setReference(event.target.value)}
            required
          />
          <Button type="submit">
            Inspect control
            <ArrowRight />
          </Button>
        </form>
      </Panel>
      <LoadState resource={resource}>
        {context && control && (
          <>
            <Panel
              title={`${control.control_id} · ${control.title}`}
              description={context.edition.edition}
            >
              <div className="tag-row">
                <Badge variant="outline">
                  Edition maturity:{" "}
                  {label(context.edition.publication.publication_status)}
                </Badge>
                <Badge variant="outline">
                  Publisher version:{" "}
                  {context.edition.publication.publisher_version ?? "Unknown"}
                </Badge>
                <Badge variant="outline">
                  Source maturity:{" "}
                  {label(control.publication.publication_status)}
                </Badge>
                <Badge variant="outline">
                  Concept SemVer:{" "}
                  {semanticVersion(
                    context.concept?.versions.at(-1)?.semantic_version,
                  )}
                </Badge>
              </div>
              <p className="concept-statement">
                {control.security_principle ??
                  "No security principle retained."}
              </p>
              <p className="table-note">
                A published mapping is not equivalence, per-clause coverage,
                compliance or enterprise adoption.
              </p>
              {context.concept && (
                <Button variant="outline" size="sm" asChild>
                  <a href={`/concepts/${context.concept.id}`}>
                    Inspect scoped governance & authority
                    <ArrowUpRight />
                  </a>
                </Button>
              )}
            </Panel>
            <Tabs defaultValue="mappings" className="detail-tabs">
              <TabsList aria-label="Control information">
                <TabsTrigger value="mappings">Published mappings</TabsTrigger>
                <TabsTrigger value="guidance">
                  Guidance & alternatives
                </TabsTrigger>
                <TabsTrigger value="policies">Policy definitions</TabsTrigger>
                <TabsTrigger value="lineage">Lineage</TabsTrigger>
                <TabsTrigger value="evidence">Source evidence</TabsTrigger>
              </TabsList>
              <TabsContent value="mappings">
                <Panel
                  title="Published identifier mappings"
                  description="Native resolution and coverage are separate facts."
                >
                  {context.control.mappings.length ? (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Framework</TableHead>
                          <TableHead>Control</TableHead>
                          <TableHead>Native resolution</TableHead>
                          <TableHead>Coverage</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {context.control.mappings.map(
                          ({ reference: mapping }, index) => (
                            <TableRow key={index}>
                              <TableCell>{mapping.framework}</TableCell>
                              <TableCell>
                                <strong>{mapping.control_id}</strong>
                                <details className="mapping-evidence">
                                  <summary>Mapping evidence</summary>
                                  <blockquote>
                                    {mapping.evidence.excerpt}
                                  </blockquote>
                                  {mapping.resolved_concept_ids.map((id) => (
                                    <a key={id} href={`/concepts/${id}`}>
                                      Inspect native control
                                      <ArrowUpRight size={14} />
                                    </a>
                                  ))}
                                </details>
                              </TableCell>
                              <TableCell>
                                <Status value={mapping.resolution} />
                              </TableCell>
                              <TableCell>{label(mapping.coverage)}</TableCell>
                            </TableRow>
                          ),
                        )}
                      </TableBody>
                    </Table>
                  ) : (
                    <Empty
                      title="No published mappings retained"
                      description="No mapping was extracted for this control."
                    />
                  )}
                </Panel>
              </TabsContent>
              <TabsContent value="guidance">
                <Panel title="Implementation guidance">
                  <p className="preserve-lines">
                    {control.azure_guidance ||
                      control.generic_guidance ||
                      "No implementation guidance retained."}
                  </p>
                  {control.implementation_options.length > 0 && (
                    <>
                      <h3>Implementation options</h3>
                      <p className="muted">
                        {control.implementation_options_are_alternatives ===
                        true
                          ? "These are alternatives, not cumulative requirements."
                          : control.implementation_options_are_alternatives ===
                              false
                            ? "The source does not identify these as alternatives."
                            : "Whether these are alternatives is unknown."}
                      </p>
                      <ul className="option-list">
                        {control.implementation_options.map((option, index) => (
                          <li key={index}>{option}</li>
                        ))}
                      </ul>
                    </>
                  )}
                  {control.subcontrols.map((child) => (
                    <details className="technical" key={child.control_id}>
                      <summary>
                        {child.control_id} · {child.title}
                      </summary>
                      <p className="preserve-lines">{child.text}</p>
                    </details>
                  ))}
                </Panel>
              </TabsContent>
              <TabsContent value="lineage">
                <BenchmarkLineage reference={selected} />
              </TabsContent>
              <TabsContent value="policies">
                <Panel
                  title="Actual policy definitions"
                  description="Published references and retrieved native definitions; not an invented IaC translation."
                >
                  {context.control.policies.length ? (
                    <div>
                      {context.control.policies.map((policy, index) => (
                        <div key={index} className="record-block">
                          <h3>{policy.reference.name}</h3>
                          <div className="tag-row">
                            <Status
                              value={
                                policy.definition ? "resolved" : "unresolved"
                              }
                            />
                            <Badge variant="outline">
                              Version match:{" "}
                              {policy.version_matches_reference === null
                                ? "Unknown"
                                : policy.version_matches_reference
                                  ? "Yes"
                                  : "No"}
                            </Badge>
                          </div>
                          {externalUrl(policy.reference.url) && (
                            <a
                              href={externalUrl(policy.reference.url)}
                              target="_blank"
                              rel="noreferrer"
                            >
                              Published policy
                              <ArrowUpRight size={14} />
                            </a>
                          )}
                          <Technical
                            value={policy}
                            title="Policy definition & reference"
                          />
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Empty
                      title="No policy references retained"
                      description="No policy definitions are linked for this control."
                    />
                  )}
                </Panel>
              </TabsContent>
              <TabsContent value="evidence">
                <Panel title="Exact published passage">
                  <blockquote>{control.evidence.excerpt}</blockquote>
                  {externalUrl(control.evidence.source_url) && (
                    <a
                      className="external-link"
                      href={externalUrl(control.evidence.source_url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Publisher location
                      <ArrowUpRight size={14} />
                    </a>
                  )}
                  <Technical
                    value={context}
                    title="Full control, provenance & identifiers"
                  />
                </Panel>
              </TabsContent>
            </Tabs>
          </>
        )}
      </LoadState>
      <Panel
        title="Compare editions"
        description="Read-only structural comparison or persisted proposals. No model calls, no automatic authority changes."
      >
        <form
          className="toolbar"
          onSubmit={(event) => {
            event.preventDefault();
            setComparison({ from, to });
          }}
        >
          <Filter
            name="From edition"
            value={from}
            onChange={setFrom}
            options={editions}
          />
          <ArrowRight size={16} aria-hidden="true" />
          <Filter
            name="To edition"
            value={to}
            onChange={setTo}
            options={editions}
          />
          <Button type="submit">
            <GitCompareArrows />
            Compare editions
          </Button>
        </form>
        {comparison && (
          <LoadState resource={changes}>
            {changes.data && (
              <>
                <div className="tag-row">
                  <Badge variant="secondary">
                    {changes.data.added_control_ids.length} added
                  </Badge>
                  <Badge variant="secondary">
                    {changes.data.removed_control_ids.length} removed
                  </Badge>
                  <Badge variant="outline">
                    {changes.data.model_calls} model calls
                  </Badge>
                </div>
                {changes.data.notes.map((note) => (
                  <p className="table-note" key={note}>
                    {note}
                  </p>
                ))}
                <div className="comparison-list">
                  {changes.data.changes.map((change, index) => (
                    <details className="comparison" key={index}>
                      <summary>
                        <span>
                          <strong>
                            {change.new?.control.control_id ??
                              change.old?.control.control_id}
                          </strong>{" "}
                          ·{" "}
                          {change.new?.control.title ??
                            change.old?.control.title}
                        </span>
                        <Badge variant="outline">{label(change.lineage)}</Badge>
                      </summary>
                      <p>
                        Proposed change: {change.proposed_change ?? "Unknown"}.
                        This is a review proposal.
                      </p>
                      <p>{change.version_note}</p>
                      <p className="muted">
                        Changed fields:{" "}
                        {change.changed_fields.map(label).join(", ") ||
                          "None recorded"}
                      </p>
                      {change.guidance_diff && (
                        <pre>{change.guidance_diff}</pre>
                      )}
                      <Technical value={change} title="Comparison details" />
                    </details>
                  ))}
                </div>
              </>
            )}
          </LoadState>
        )}
      </Panel>
    </>
  );
}

function BenchmarkLineage({ reference }: { reference: string }) {
  const resource = useResource<{
    published_edition_lineage: {
      successor: string;
      predecessor: string;
      evidence: { excerpt: string; source_url: string };
    }[];
    semantic_proposals: {
      comparison_id: string;
      proposal: {
        lineage: string;
        proposed_change: string | null;
        version_note: string;
        verification: unknown;
      };
    }[];
  }>(`/api/benchmarks/controls/${encodeURIComponent(reference)}/lineage`);
  return (
    <Panel
      title="Published succession & semantic lineage"
      description="Publisher assertions and review-only semantic proposals are deliberately separate."
    >
      <LoadState resource={resource}>
        {resource.data && (
          <>
            <h3>Published edition succession</h3>
            {resource.data.published_edition_lineage.length ? (
              resource.data.published_edition_lineage.map((edge, index) => (
                <article className="evidence" key={index}>
                  <div className="tag-row">
                    <Badge variant="outline">{edge.successor}</Badge>
                    <ArrowRight size={16} />
                    <span>Supersedes, published</span>
                    <ArrowRight size={16} />
                    <Badge variant="outline">{edge.predecessor}</Badge>
                  </div>
                  <blockquote>{edge.evidence.excerpt}</blockquote>
                  {externalUrl(edge.evidence.source_url) && (
                    <a
                      className="external-link"
                      href={externalUrl(edge.evidence.source_url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Succession evidence
                      <ArrowUpRight size={14} />
                    </a>
                  )}
                </article>
              ))
            ) : (
              <p className="muted">No verified succession passage retained.</p>
            )}
            <h3>Semantic lineage candidates</h3>
            {resource.data.semantic_proposals.length ? (
              resource.data.semantic_proposals.map((entry, index) => (
                <div className="record-block" key={index}>
                  <Badge variant="secondary">
                    {label(entry.proposal.lineage)} · Proposed
                  </Badge>
                  <p>{entry.proposal.version_note}</p>
                  <Technical
                    value={entry}
                    title="Proposal, verification & exact evidence"
                  />
                </div>
              ))
            ) : (
              <p className="muted">
                No saved semantic lineage proposals. The CLI comparison command
                can persist review-only candidates.
              </p>
            )}
          </>
        )}
      </LoadState>
    </Panel>
  );
}
