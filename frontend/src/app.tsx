import { useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  Blocks,
  Building2,
  CheckCheck,
  ChevronRight,
  CircleHelp,
  CloudCog,
  Database,
  Files,
  Layers3,
  LayoutDashboard,
  Menu,
  Moon,
  ShieldCheck,
  Sun,
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
import { label, useResource } from "@/lib/api";
import { Knowledge, ConceptPage } from "@/knowledge";
import { Review } from "@/review";
import { Sources, SourcePage } from "@/sources";
import { Explorer } from "@/network";
import { DataExplorer, Logs, ReconciliationLog } from "@/reference";
import { Ontology } from "@/ontology";
import type { Issue, Source } from "@/types";

const navigation = [
  {
    href: "/",
    name: "Consolidation",
    icon: LayoutDashboard,
    group: "Workspace",
  },
  {
    href: "/ontology",
    name: "Shared language",
    icon: Layers3,
    group: "Workspace",
  },
  { href: "/sources", name: "Sources", icon: Files, group: "Consolidate" },
  {
    href: "/company",
    name: "Company intent",
    icon: Building2,
    group: "Knowledge",
  },
  {
    href: "/architecture",
    name: "Platform-agnostic",
    icon: Blocks,
    group: "Knowledge",
  },
  {
    href: "/platforms",
    name: "Platforms & controls",
    icon: CloudCog,
    group: "Knowledge",
  },
  {
    href: "/explore",
    name: "Visual validation",
    icon: Network,
    group: "Consolidate",
  },
  {
    href: "/review",
    name: "Reconciliation backlog",
    icon: CheckCheck,
    group: "Consolidate",
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
  const conceptView = query.get("view");
  const section = path.startsWith("/concepts/")
    ? conceptView === "architecture"
      ? "/architecture"
      : conceptView === "platforms"
        ? "/platforms"
        : "/company"
    : path.startsWith("/sources/")
      ? "/sources"
      : path === "/" && query.has("q")
        ? "/company"
        : path;
  const active =
    navigation.find((item) => item.href === section) ?? navigation[0];
  const [menu, setMenu] = useState(false);
  const [dark, setDark] = useState(
    document.documentElement.dataset.theme === "dark",
  );
  const config = useResource<{
    governance_enabled: boolean;
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
            <small>Architecture standards</small>
          </span>
        </a>
        {[
          "Workspace",
          "Consolidate",
          "Knowledge",
          "Reference",
          "Operations",
        ].map((group) => (
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
          {section === "/company" &&
            (path.startsWith("/concepts/") ? (
              <ConceptPage id={path.split("/")[2]} governance={governance} />
            ) : (
              <Knowledge view="company" />
            ))}
          {section === "/architecture" &&
            (path.startsWith("/concepts/") ? (
              <ConceptPage id={path.split("/")[2]} governance={governance} />
            ) : (
              <Knowledge view="architecture" />
            ))}
          {section === "/platforms" &&
            (path.startsWith("/concepts/") ? (
              <ConceptPage id={path.split("/")[2]} governance={governance} />
            ) : (
              <Knowledge view="platforms" />
            ))}
          {section === "/knowledge" && <Knowledge view="all" />}
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
          {section === "/ontology" && <Ontology />}
        </main>
        <footer className="workspace-footer">
          <ShieldCheck size={14} />
          <span>
            Knowledge candidates are not approved enterprise policy. Publication
            maturity and scoped adoption are separate decisions.
          </span>
        </footer>
      </div>
    </div>
  );
}

function Overview() {
  const sources = useResource<Source[]>("/api/sources");
  const issues = useResource<Issue[]>("/api/issues?status=open&limit=1000");
  return (
    <>
      <Heading
        eyebrow="ARCHITECTURE STANDARDS WORKSPACE"
        title="Consolidate architecture standards."
        description="Establish a shared language, inspect source-grounded interpretations and decide what applies to your organization."
      >
        <Button asChild>
          <a href="/ontology">
            <Layers3 />
            Start with shared language
          </a>
        </Button>
      </Heading>
      <Panel
        title="From source material to scoped standards"
        description="A review path, not a completion tracker. Ingestion produces candidates; it does not approve standards."
      >
        <ol
          className="consolidation-journey"
          aria-label="Standards consolidation workflow"
        >
          {[
            [
              "/sources",
              "Curate sources",
              "Inspect publisher, edition, retrieval and document maturity. Source selection is operator-configured.",
            ],
            [
              "/company",
              "Clarify company intent",
              "Review the small set of enterprise goals and fundamental obligations before designing solutions.",
            ],
            [
              "/architecture",
              "Shape platform-agnostic architecture",
              "Organize domains, conceptual requirements and vendor-neutral technical constraints.",
            ],
            [
              "/platforms",
              "Inspect platform controls",
              "Evaluate product-specific implementations without mistaking publisher guidance for adoption.",
            ],
            [
              "/explore",
              "Validate relationships",
              "Inspect supporting evidence, direction and proposed links. A mapping is not equivalence.",
            ],
            [
              "/review",
              "Decide applicability",
              "Resolve duplicates and conflicts; record explicit, scoped decisions with a rationale.",
            ],
          ].map(([href, title, description], index) => (
            <li key={href}>
              <a href={href}>
                <span className="journey-number" aria-hidden="true">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <h3>
                  {title}
                  <ArrowUpRight size={15} aria-hidden="true" />
                </h3>
                <p>{description}</p>
              </a>
            </li>
          ))}
        </ol>
      </Panel>
      <div className="metrics-grid overview-metrics">
        <Metric
          title="Registered sources"
          value={sources.data?.length}
          detail="Publisher editions remain separate"
        />
        <Metric
          title="Available sources"
          value={
            sources.data?.filter((source) => source.status === "available")
              .length
          }
          detail="Original document identities are retained"
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
      </div>
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
        <LoadState resource={issues}>
          {issues.data?.length === 0 ? (
            <Empty
              title="No open issues recorded"
              description="This is not evidence of coverage or approval. Inspect the sources and evidence before concluding that review is complete."
            />
          ) : (
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
          )}
          {!!issues.data?.length && (
            <p className="table-note">
              Showing {Math.min(5, issues.data.length)} of {issues.data.length}
              {issues.data.length === 1000 ? "+" : ""} loaded open issues.
            </p>
          )}
        </LoadState>
      </Panel>
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
      {sources.error && (
        <p role="alert" className="muted">
          Some workspace metrics are unavailable.{" "}
          <button
            className="text-button"
            onClick={() => {
              sources.reload();
            }}
          >
            Retry metrics
          </button>
        </p>
      )}
    </>
  );
}
