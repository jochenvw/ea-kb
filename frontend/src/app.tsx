import { useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  CheckCheck,
  ChevronRight,
  CircleHelp,
  Database,
  Files,
  GitCompareArrows,
  Layers3,
  LayoutDashboard,
  Menu,
  Moon,
  ShieldCheck,
  Sun,
  Workflow,
  Network,
  ScrollText,
  History,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Heading,
  LoadState,
  Metric,
  Panel,
  Status,
  Empty,
} from "@/components/workspace";
import { date, label, useResource } from "@/lib/api";
import { Knowledge, ConceptPage } from "@/knowledge";
import { Review } from "@/review";
import { Sources, SourcePage } from "@/sources";
import { Explorer } from "@/network";
import { DataExplorer, Logs, ReconciliationLog } from "@/reference";
import { Activity } from "@/activity";
import { Benchmarks } from "@/benchmarks";
import type { Issue, Pipeline, SourceGroup } from "@/types";

const navigation = [
  { href: "/", name: "Overview", icon: LayoutDashboard, group: "Workspace" },
  {
    href: "/review",
    name: "Reconciliation backlog",
    icon: CheckCheck,
    group: "Work",
  },
  { href: "/pipeline", name: "Pipeline", icon: Workflow, group: "Work" },
  { href: "/sources", name: "Sources", icon: Files, group: "Reference" },
  { href: "/knowledge", name: "Knowledge", icon: BookOpen, group: "Reference" },
  {
    href: "/explore",
    name: "Visual explorer",
    icon: Network,
    group: "Reference",
  },
  {
    href: "/benchmarks",
    name: "Benchmarks",
    icon: GitCompareArrows,
    group: "Reference",
  },
  {
    href: "/reconciliation",
    name: "Reconciliation log",
    icon: History,
    group: "Reference",
  },
  {
    href: "/data",
    name: "Data inspector",
    icon: Database,
    group: "Operations",
  },
  {
    href: "/logs",
    name: "Operational logs",
    icon: ScrollText,
    group: "Operations",
  },
];

export function App() {
  const path = window.location.pathname;
  const query = new URLSearchParams(window.location.search);
  const section = path.startsWith("/concepts/")
    ? "/knowledge"
    : path.startsWith("/sources/")
      ? "/sources"
      : path === "/" && query.has("q")
        ? "/knowledge"
        : path;
  const active =
    navigation.find((item) => item.href === section) ?? navigation[0];
  const [menu, setMenu] = useState(false);
  const [dark, setDark] = useState(
    document.documentElement.dataset.theme === "dark",
  );
  const config = useResource<{
    governance_enabled: boolean;
    scan_running: boolean;
  }>("/api/ui/config");
  const governance = config.data?.governance_enabled === true;

  function toggleTheme() {
    const theme = dark ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    setDark(!dark);
    try {
      localStorage.setItem("ekb-theme", theme);
    } catch {
      /* Storage is optional. */
    }
  }

  return (
    <div className="workspace">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className={`sidebar ${menu ? "is-open" : ""}`}>
        <a href="/" className="brand">
          <span className="brand-mark">
            <Layers3 />
          </span>
          <span>
            <strong>EKB</strong>
            <small>Architecture knowledge</small>
          </span>
        </a>
        {["Workspace", "Work", "Reference", "Operations"].map((group) => (
          <div className="nav-group" key={group}>
            <p className="nav-label">{group.toUpperCase()}</p>
            <nav aria-label={`${group} navigation`}>
              {navigation
                .filter((item) => item.group === group)
                .map((item) => (
                  <a
                    key={item.href}
                    href={item.href}
                    aria-current={section === item.href ? "page" : undefined}
                  >
                    <item.icon aria-hidden="true" />
                    {item.name}
                    {section === item.href && (
                      <ChevronRight className="nav-arrow" />
                    )}
                  </a>
                ))}
            </nav>
          </div>
        ))}
        <div className="sidebar-bottom">
          <Separator />
          <div className="workspace-label">
            <Database size={16} />
            <span>
              Local workspace<small>Evidence stays in your store</small>
            </span>
          </div>
          <a href="/docs" className="api-link">
            <CircleHelp size={16} />
            API reference
            <ArrowUpRight size={14} />
          </a>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <Button
              className="mobile-menu"
              variant="ghost"
              size="icon"
              aria-label="Toggle navigation"
              aria-expanded={menu}
              onClick={() => setMenu(!menu)}
            >
              <Menu />
            </Button>
            <span>{active.group}</span>
            <ChevronRight size={14} />
            <strong>{active.name}</strong>
          </div>
          <div className="topbar-actions">
            <Badge variant="outline">
              <ShieldCheck size={13} />
              {governance ? "Governed writes enabled" : "Read-only governance"}
            </Badge>
            <Button
              variant="ghost"
              size="icon"
              aria-label={dark ? "Use light theme" : "Use dark theme"}
              onClick={toggleTheme}
            >
              {dark ? <Sun /> : <Moon />}
            </Button>
          </div>
        </header>
        <main id="main">
          {section === "/" && <Overview />}
          {section === "/knowledge" &&
            (path.startsWith("/concepts/") ? (
              <ConceptPage id={path.split("/")[2]} governance={governance} />
            ) : (
              <Knowledge />
            ))}
          {section === "/review" && <Review governance={governance} />}
          {section === "/sources" &&
            (path.startsWith("/sources/") ? (
              <SourcePage id={path.split("/")[2]} />
            ) : (
              <Sources />
            ))}
          {section === "/explore" && <Explorer />}
          {section === "/reconciliation" && <ReconciliationLog />}
          {section === "/data" && <DataExplorer />}
          {section === "/logs" && <Logs />}
          {section === "/pipeline" && (
            <Activity scanRunning={config.data?.scan_running === true} />
          )}
          {section === "/benchmarks" && <Benchmarks />}
        </main>
        <footer className="workspace-footer">
          <ShieldCheck size={14} />
          <span>
            Interpretations are candidates, not approved enterprise policy.
            Publication maturity and scoped adoption are separate decisions.
          </span>
        </footer>
      </div>
    </div>
  );
}

function Overview() {
  const sources = useResource<SourceGroup[]>("/api/source-groups");
  const issues = useResource<Issue[]>("/api/issues?status=open&limit=1000");
  const pipeline = useResource<Pipeline | null>("/api/pipeline/status");
  const stats = useResource<{
    task_count: number;
    failures: number;
    input_tokens: number;
    output_tokens: number;
    configured_cost: number | null;
  }>("/api/statistics");
  return (
    <>
      <Heading
        eyebrow="WORKSPACE OVERVIEW"
        title="Knowledge, with evidence."
        description="Explore architecture knowledge, trace its sources and resolve what needs attention."
      >
        <Button variant="outline" asChild>
          <a href="/knowledge">
            <BookOpen />
            Explore knowledge
          </a>
        </Button>
        <Button asChild>
          <a href="/review?view=issues">
            <CheckCheck />
            Review issues
          </a>
        </Button>
      </Heading>
      <div className="metrics-grid">
        <Metric
          title="Registered sources"
          value={sources.data?.length}
          detail={`${sources.data?.reduce((count, group) => count + group.documents.length, 0) ?? "…"} evidence documents; one source per edition`}
        />
        <Metric
          title="Open issues"
          value={
            issues.data
              ? `${issues.data.length.toLocaleString()}${issues.data.length === 1000 ? "+" : ""}`
              : undefined
          }
          detail="Human review needed; not a compliance verdict"
        />
        <Metric
          title="Latest pipeline"
          value={
            pipeline.data
              ? label(pipeline.data.run.status)
              : pipeline.loading || pipeline.error
                ? undefined
                : "Not run"
          }
          detail="Indexing → enrichment → issues"
        />
        <Metric
          title="Recent processing tasks"
          value={stats.data?.task_count}
          detail="Up to the latest 1,000 persisted tasks"
        />
      </div>
      <div className="overview-grid">
        <Panel
          title="Needs your attention"
          description="Open issues, with higher severity first."
          action={
            <Button variant="ghost" size="sm" asChild>
              <a href="/review?view=issues">
                View queue
                <ArrowUpRight />
              </a>
            </Button>
          }
        >
          <LoadState resource={issues} empty={issues.data?.length === 0}>
            <div className="attention-list">
              {[...(issues.data ?? [])]
                .sort(
                  (a, b) =>
                    ["critical", "high", "medium", "low"].indexOf(a.severity) -
                    ["critical", "high", "medium", "low"].indexOf(b.severity),
                )
                .slice(0, 5)
                .map((issue) => (
                  <a
                    key={issue.id}
                    href={`/review?issue_id=${issue.id}`}
                    className="attention-item"
                  >
                    <span
                      className={`severity-mark severity-${issue.severity}`}
                      aria-hidden="true"
                    />
                    <span>
                      <strong>{label(issue.issue_type)}</strong>
                      <small>{issue.explanation}</small>
                    </span>
                    <Status value={issue.severity} />
                    <ChevronRight size={16} />
                  </a>
                ))}
            </div>
            {issues.data && (
              <p className="table-note">
                Showing {Math.min(5, issues.data.length)} of{" "}
                {issues.data.length}
                {issues.data.length === 1000 ? "+" : ""} loaded open issues.
              </p>
            )}
          </LoadState>
        </Panel>
        <Panel
          title="Latest pipeline"
          description="Durable stages, without automatic promotion."
          action={
            <Button variant="ghost" size="icon" asChild>
              <a href="/pipeline" aria-label="View pipeline activity">
                <ArrowUpRight />
              </a>
            </Button>
          }
        >
          <LoadState resource={pipeline}>
            {pipeline.data ? (
              <>
                <div className="stage-list">
                  {["indexing", "enrichment", "issues"].map((stage, index) => {
                    const execution = pipeline.data!.executions.find(
                      (item) =>
                        item.kind === "stage" && item.processor_id === stage,
                    );
                    return (
                      <div key={stage}>
                        <span className="stage-number">{index + 1}</span>
                        <span>
                          <strong>{label(stage)}</strong>
                          <small>
                            {
                              [
                                "Originals, segments and candidates",
                                "Evidence-backed reconciliation",
                                "Persistent, reviewable exceptions",
                              ][index]
                            }
                          </small>
                        </span>
                        <Status value={execution?.status ?? "not_run"} />
                      </div>
                    );
                  })}
                </div>
                <p className="table-note">
                  Started {date(pipeline.data.run.started_at)}
                </p>
              </>
            ) : (
              <Empty
                title="No pipeline run recorded"
                description="Run ekb pipeline run to process all three stages."
              />
            )}
          </LoadState>
        </Panel>
      </div>
      <Panel
        title="Evidence before authority"
        description="Three distinct questions. Never one approval badge."
      >
        <div className="principle-grid">
          {[
            [
              "01",
              "What did the source say?",
              "Exact excerpts, locations, original hashes and retrieval history.",
            ],
            [
              "02",
              "What does it mean?",
              "Candidate interpretations and mappings, with uncertainty retained.",
            ],
            [
              "03",
              "What applies here?",
              "Explicit, scoped governance decisions. Never inferred from publication.",
            ],
          ].map(([number, title, text]) => (
            <div key={number}>
              <span className="eyebrow">{number}</span>
              <h3>{title}</h3>
              <p>{text}</p>
            </div>
          ))}
        </div>
      </Panel>
      {(sources.error || stats.error) && (
        <p role="alert" className="muted">
          Some workspace metrics are unavailable.{" "}
          <button
            className="text-button"
            onClick={() => {
              sources.reload();
              stats.reload();
            }}
          >
            Retry metrics
          </button>
        </p>
      )}
    </>
  );
}
