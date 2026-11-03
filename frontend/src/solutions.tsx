import { ArrowLeft, ArrowUpRight, Download, FileCheck2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Empty,
  Heading,
  LoadState,
  Metric,
  Panel,
  ScopeInfo,
} from "@/components/workspace";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  assertionText,
  date,
  externalUrl,
  label,
  useResource,
} from "@/lib/api";
import type {
  ArchitectureRequirementDetail,
  AssessmentFinding,
  RetainedAssessment,
  SolutionArchitecture,
} from "@/types";

export function Solutions() {
  const resource = useResource<SolutionArchitecture[]>("/api/architectures");
  return (
    <>
      <Heading
        eyebrow="DELIVERY / SOLUTION ARCHITECTURES"
        title="Register solution architecture decisions."
        description="Keep immutable architecture artefacts, canonical pointers, applicable standards and approved exceptions together. Registration is explicit; absence is not compliance."
      />
      <Panel
        title="Solution architecture registry"
        description="Latest revision per registered architecture. Use the CLI to add a YAML manifest and its immutable artefact."
      >
        <LoadState resource={resource} empty={resource.data?.length === 0}>
          {resource.data?.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Architecture</TableHead>
                  <TableHead>Version</TableHead>
                  <TableHead>Owner</TableHead>
                  <TableHead>Requirements</TableHead>
                  <TableHead>Exceptions</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {resource.data.map((architecture) => {
                  const latest = architecture.revisions.at(-1)!;
                  return (
                    <TableRow key={architecture.id}>
                      <TableCell>
                        <a
                          className="statement-link"
                          href={`/solutions/${architecture.id}`}
                        >
                          {latest.name}
                        </a>
                        <small className="cell-caption">
                          {latest.description}
                        </small>
                      </TableCell>
                      <TableCell>{latest.version}</TableCell>
                      <TableCell>{latest.owner}</TableCell>
                      <TableCell>{latest.controls.length}</TableCell>
                      <TableCell>
                        {
                          latest.controls.filter(
                            (control) => control.disposition === "exception",
                          ).length
                        }
                      </TableCell>
                      <TableCell>
                        <Button variant="ghost" size="icon" asChild>
                          <a
                            href={`/solutions/${architecture.id}`}
                            aria-label={`Inspect ${latest.name}`}
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
          ) : (
            <Empty
              title="No solution architectures registered"
              description="Register the first revision with `ekb architectures register <manifest.yaml> <artifact>`."
            />
          )}
        </LoadState>
      </Panel>
    </>
  );
}

export function SolutionPage({ id }: { id: string }) {
  const architecture = useResource<SolutionArchitecture>(
    `/api/architectures/${encodeURIComponent(id)}`,
  );
  const requirements = useResource<ArchitectureRequirementDetail[]>(
    `/api/architectures/${encodeURIComponent(id)}/requirements`,
  );
  const latest = architecture.data?.revisions.at(-1);
  return (
    <>
      <Button variant="ghost" size="sm" asChild className="back-link">
        <a href="/solutions">
          <ArrowLeft />
          Solution registry
        </a>
      </Button>
      <Heading
        eyebrow="SOLUTION ARCHITECTURE"
        title={latest?.name ?? "Architecture revision"}
        description={
          latest?.description ??
          "Inspect the registered artefact, applicable requirements and explicit exceptions."
        }
      />
      <LoadState resource={architecture}>
        {latest && (
          <>
            <div className="two-column">
              <Panel title="Registered revision">
                <div className="tag-row">
                  <Badge variant="outline">Version {latest.version}</Badge>
                  <Badge variant="secondary">{latest.owner}</Badge>
                  <Badge variant="outline">
                    {latest.controls.length} requirements
                  </Badge>
                </div>
                <p className="table-note">
                  Recorded {date(latest.recorded_at)} · SHA-256{" "}
                  {latest.content_hash.slice(0, 12)}…
                </p>
                <ScopeInfo scope={latest.scope} />
                <Button variant="outline" size="sm" asChild>
                  <a
                    href={`/api/architectures/${id}/revisions/${latest.id}/artifact`}
                  >
                    <Download />
                    Download {latest.artifact_name}
                  </a>
                </Button>
              </Panel>
              <Panel
                title="Canonical source"
                description="The registry retains a pointer; it does not claim ownership of the canonical authoring system."
              >
                {externalUrl(latest.canonical_source) ? (
                  <a
                    className="external-link"
                    href={latest.canonical_source}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {latest.canonical_source}
                    <ArrowUpRight size={14} />
                  </a>
                ) : (
                  <code>{latest.canonical_source}</code>
                )}
              </Panel>
            </div>
            <Panel
              title="Applicable requirements & exceptions"
              description="Required controls need implementation evidence. Exceptions are explicit, approved and revision-bound."
            >
              <LoadState
                resource={requirements}
                empty={requirements.data?.length === 0}
              >
                {requirements.data?.length ? (
                  <div className="architecture-requirements">
                    {requirements.data.map(({ control, concept }) => {
                      const version = concept.versions.find(
                        (item) => item.id === control.concept_version_id,
                      );
                      const level = version
                        ? concept.levels?.[version.id]
                        : undefined;
                      const view =
                        level === "platform_specific"
                          ? "platforms"
                          : "architecture";
                      return (
                        <article key={control.concept_id}>
                          <FileCheck2 size={16} />
                          <div>
                            <a
                              href={`/concepts/${control.concept_id}?view=${view}`}
                            >
                              {assertionText(version)}
                            </a>
                            <p>
                              {control.disposition === "required"
                                ? "Required"
                                : `Approved exception · ${control.approved_by}${control.expires_on ? ` · until ${control.expires_on}` : ""}`}
                            </p>
                            {control.rationale && (
                              <small>{control.rationale}</small>
                            )}
                          </div>
                          <Badge
                            variant={
                              control.disposition === "exception"
                                ? "secondary"
                                : "outline"
                            }
                          >
                            {control.disposition}
                          </Badge>
                        </article>
                      );
                    })}
                  </div>
                ) : (
                  <Empty title="No requirements recorded" />
                )}
              </LoadState>
            </Panel>
            <Assessments id={id} />
            <Panel
              title="Revision history"
              description="Re-register changes as a new immutable version; prior exceptions and artefacts remain auditable."
            >
              {architecture.data?.revisions.map((revision) => (
                <div className="record-block" key={revision.id}>
                  <div className="tag-row">
                    <Badge variant="outline">Version {revision.version}</Badge>
                    <span className="muted">{date(revision.recorded_at)}</span>
                  </div>
                  <p>
                    {revision.artifact_name} · {revision.controls.length}{" "}
                    requirements
                  </p>
                </div>
              ))}
            </Panel>
          </>
        )}
      </LoadState>
    </>
  );
}

const DISPOSITION_VARIANT: Record<
  AssessmentFinding["disposition"],
  "default" | "secondary" | "destructive" | "outline"
> = {
  conforms: "default",
  excepted: "secondary",
  violated: "destructive",
  unknown: "outline",
  not_applicable: "outline",
};

function Assessments({ id }: { id: string }) {
  const resource = useResource<RetainedAssessment[]>(
    `/api/architectures/${encodeURIComponent(id)}/assessments?limit=10`,
  );
  const [latest, ...earlier] = resource.data ?? [];
  const report = latest?.report;
  return (
    <Panel
      title="Latest verified assessment"
      description="Retained exactly as produced by `ekb assess`, the MCP assess_solution tool or the API. Unknown is never a pass."
    >
      <LoadState resource={resource} empty={resource.data?.length === 0}>
        {report ? (
          <>
            <div className="tag-row">
              <Badge variant={report.conforms ? "default" : "destructive"}>
                {report.conforms ? "Conforms" : "Conformance not established"}
              </Badge>
              {report.acceptable_with_exceptions && !report.conforms && (
                <Badge variant="secondary">Acceptable with exceptions</Badge>
              )}
              {!report.baseline_complete && (
                <Badge variant="outline">Baseline authority incomplete</Badge>
              )}
            </div>
            <p className="table-note">
              Assessed {date(latest.assessed_at)} · as of {report.target.as_of}
              {report.target.commit
                ? ` · commit ${report.target.commit}`
                : ""}{" "}
              · template SHA-256 {report.target.artifact_sha256.slice(0, 12)}… ·
              baseline {report.baseline_revision.slice(0, 12)}…
            </p>
            <div className="metrics-grid">
              <Metric
                title="Applicable"
                value={report.coverage.applicable}
                detail={`of ${report.coverage.total} baseline requirements`}
              />
              <Metric
                title="Violated"
                value={report.coverage.violated}
                detail="design or implementation evidence fails"
              />
              <Metric
                title="Excepted"
                value={report.coverage.excepted}
                detail="active approved exceptions"
              />
              <Metric
                title="Unknown"
                value={report.coverage.unresolved}
                detail="authority, design or evidence unresolved"
              />
            </div>
            {report.gaps.length > 0 && (
              <ul className="assessment-gaps">
                {report.gaps.map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            )}
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Requirement</TableHead>
                  <TableHead>Disposition</TableHead>
                  <TableHead>Design</TableHead>
                  <TableHead>Implementation</TableHead>
                  <TableHead>Evidence</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {report.findings.map((finding) => (
                  <TableRow key={finding.id}>
                    <TableCell>
                      <a
                        className="statement-link"
                        href={`/concepts/${finding.requirement.concept_id}`}
                      >
                        {finding.requirement.statement}
                      </a>
                      <small className="cell-caption">
                        {finding.explanation}
                        {finding.remediation
                          ? ` Remediation: ${finding.remediation}`
                          : ""}
                      </small>
                      {finding.chain.length > 0 && (
                        <small className="cell-caption">
                          Traces to{" "}
                          {finding.chain
                            .map(
                              (step) =>
                                `${label(step.level)}: ${step.statement}`,
                            )
                            .join(" → ")}
                        </small>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant={DISPOSITION_VARIANT[finding.disposition]}>
                        {label(finding.disposition)}
                      </Badge>
                      {finding.exception && (
                        <small className="cell-caption">
                          Approved by {finding.exception.approved_by}
                          {finding.exception.effective_until
                            ? ` until ${finding.exception.effective_until}`
                            : ""}
                        </small>
                      )}
                    </TableCell>
                    <TableCell>{label(finding.design)}</TableCell>
                    <TableCell>{label(finding.implementation)}</TableCell>
                    <TableCell>
                      {finding.decision_location && (
                        <code>{finding.decision_location}</code>
                      )}
                      {finding.resources.map((resource) => (
                        <small className="cell-caption" key={resource}>
                          {resource}
                        </small>
                      ))}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {earlier.length > 0 && (
              <div className="record-block">
                <p>Earlier retained reports</p>
                {earlier.map((item) => (
                  <p className="muted" key={item.id}>
                    {date(item.assessed_at)} ·{" "}
                    {item.report.conforms ? "conforms" : "not established"} ·{" "}
                    {item.report.fingerprint.slice(0, 12)}…
                  </p>
                ))}
              </div>
            )}
          </>
        ) : (
          <Empty
            title="No retained assessments"
            description="Run `ekb assess <architecture> <template.json> --baseline-concept … --baseline-version …` or the MCP assess_solution tool."
          />
        )}
      </LoadState>
    </Panel>
  );
}
