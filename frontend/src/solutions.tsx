import { ArrowLeft, ArrowUpRight, Download, FileCheck2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Empty,
  Heading,
  LoadState,
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
import { assertionText, date, externalUrl, useResource } from "@/lib/api";
import type {
  ArchitectureRequirementDetail,
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
