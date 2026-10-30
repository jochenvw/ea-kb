import { test, expect, type Page } from "@playwright/test";

const id = "11111111-1111-4111-8111-111111111111";
const findingId = "22222222-2222-4222-8222-222222222222";
const relatedId = "33333333-3333-4333-8333-333333333333";
const sourceId = "44444444-4444-4444-8444-444444444444";
const timestamp = "2026-01-01T10:00:00Z";
const scope = { environment: "test", organization: null };
const publication = {
  publication_status: "draft",
  publisher_version: "v2",
  semantic_version: null,
};
const statement = "Teams must retain audit logs.";
const namespace = "https://example.org/ekb/ontology#";
const ontology = {
  iri: "https://example.org/ekb/ontology",
  namespace,
  label: "Enterprise Knowledge Base ontology",
  definition: "Shared enterprise architecture vocabulary.",
  version: "0.1.0-draft.3",
  sha256: "a".repeat(64),
  terms: [
    {
      name: "TechnicalRequirement",
      label: "Technical requirement",
      definition:
        "A specific, technically actionable condition or constraint that remains independent of any particular vendor, product, or platform.",
      kind: "class",
      category: "concept",
    },
    {
      name: "OrganizationalScope",
      label: "Organizational scope",
      definition:
        "An organizational entity or area, such as an enterprise, division, business unit, or department, within which goals and requirements may apply.",
      kind: "class",
      category: "concept",
    },
    {
      name: "supports",
      label: "supports",
      definition:
        "Indicates that satisfying or achieving one concept contributes to achieving another, typically a goal or desired outcome.",
      kind: "object_property",
      category: "relation",
    },
    {
      name: "subScopeOf",
      label: "sub-scope of",
      definition:
        "Indicates that an organizational scope is contained within another organizational scope, without automatically implying inherited regulatory applicability.",
      kind: "object_property",
      category: "classification",
      characteristics: ["transitive"],
      domains: [namespace + "OrganizationalScope"],
      ranges: [namespace + "OrganizationalScope"],
    },
    {
      name: "platformName",
      label: "platform name",
      definition:
        "Identifies the technology platform to which a platform-specific requirement applies.",
      kind: "datatype_property",
      category: "classification",
      ranges: ["http://www.w3.org/2001/XMLSchema#string"],
    },
    {
      name: "StrategicGoal",
      label: "Strategic goal",
      definition:
        "An outcome the enterprise or one of its organizational units deliberately pursues.",
      kind: "class",
      category: "concept",
    },
    {
      name: "StrategicRequirement",
      label: "Strategic requirement",
      definition:
        "A fundamental obligation or constraint that the enterprise or one of its organizational units must satisfy or has formally committed to uphold.",
      kind: "class",
      category: "concept",
    },
    {
      name: "ConceptualRequirement",
      label: "Conceptual requirement",
      definition:
        "A desired condition or constraint expressed independently of specific technologies, platforms, or implementation mechanisms.",
      kind: "class",
      category: "concept",
    },
    {
      name: "PlatformRequirement",
      label: "Platform requirement",
      definition:
        "A technical condition, constraint, or recommendation applicable to a particular technology platform, such as Microsoft Azure or AWS.",
      kind: "class",
      category: "concept",
    },
    {
      name: "ArchitectureDomain",
      label: "Architecture domain",
      definition:
        "A technology-independent area of architectural concern, such as network security, identity management, or data protection.",
      kind: "class",
      category: "concept",
    },
  ].map((term) => ({
    iri: namespace + term.name,
    domains: [],
    ranges: [],
    characteristics: [],
    ...term,
  })),
};
const excerpt = "Teams must retain <em>audit logs</em>.";
const source = {
  item: {
    id: sourceId,
    name: "Example architecture standard",
    location: "https://example.com/standard",
    format: "md",
    metadata: { publisher: "Example publisher" },
  },
  status: "available",
  checked_at: timestamp,
  revision_id: id,
  error: null,
  publication,
};
const sourceGroup = {
  id: sourceId,
  name: source.item.name,
  publisher: "Example publisher",
  edition: null,
  documents: [source],
};
const concept = {
  id,
  levels: { [id]: "top_level" },
  versions: [
    {
      id,
      candidate: {
        statement,
        semantic_type: "TechnicalRequirement",
        modality: "mandated",
        confidence: 0.9,
        uncertainty: "Synthetic fixture; human review required.",
        scope,
      },
      publication,
      semantic_version: null,
      recorded_at: timestamp,
    },
  ],
  occurrences: [{ id, excerpt, start: 5, end: 5 + excerpt.length }],
  authority_states: [
    {
      concept_version_id: id,
      governance_status: "approved",
      canonical_status: "not_canonical",
      scope,
      effective_from: "2020-01-01",
      effective_until: "2099-12-31",
    },
  ],
  authority_events: [],
  relationships: [],
};
const issue = {
  id,
  issue_type: "unresolved_duplicate",
  severity: "medium",
  status: "open",
  confidence: 0.85,
  explanation: "Two possible duplicates need an explicit identity decision.",
  observed: true,
  owner: null,
  scope,
  concept_ids: [id],
  finding_ids: [findingId],
  options: ["Review source evidence"],
  last_observed: timestamp,
};
const finding = {
  id: findingId,
  check_id: "duplicate_identity",
  check_version: "1",
  action: "alias",
  relationship: null,
  concept_ids: [id],
  version_ids: [id],
  explanation: "A possible duplicate requires evidence review.",
  confidence: 0.85,
  uncertainty: "Scope remains uncertain.",
  scope,
  passages: [],
  recorded_at: timestamp,
};
const execution = {
  id,
  kind: "pipeline",
  status: "done",
  started_at: timestamp,
  finished_at: timestamp,
  attempts: 1,
  statistics: {},
};

async function fixture(page: Page, governance = false) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = decodeURIComponent(url.pathname);
    let data: unknown;
    if (path === "/api/ui/config")
      data = { governance_enabled: governance, scan_running: false };
    else if (path === "/api/ontology") data = ontology;
    else if (path === "/api/sources") data = [source];
    else if (path === "/api/source-groups") data = [sourceGroup];
    else if (path === `/api/sources/${sourceId}`)
      data = {
        source,
        revisions: 1,
        segments: 2,
        versions: 1,
        occurrences: 1,
        items: [
          { concept_id: id, version: concept.versions[0], evidence_count: 1 },
        ],
        next_cursor: null,
      };
    else if (path === "/api/concepts")
      data = statement
        .toLowerCase()
        .includes((url.searchParams.get("q") ?? "").toLowerCase())
        ? [concept]
        : [];
    else if (path === `/api/concepts/${id}`) data = concept;
    else if (
      path === `/api/concepts/${id}/graph` ||
      path === `/api/concepts/${relatedId}/graph`
    )
      data = {
        root: path.includes(relatedId) ? relatedId : id,
        nodes: [
          {
            id,
            label: statement,
            kind: "requirement",
            level: "top_level",
            href: `/concepts/${id}`,
          },
          {
            id: relatedId,
            label: "An associated security objective.",
            kind: "objective",
            level: "top_level",
            href: `/concepts/${relatedId}`,
          },
          {
            id: sourceId,
            label: source.item.name,
            kind: "source",
            href: `/sources/${sourceId}`,
          },
        ],
        edges: [
          {
            source: sourceId,
            target: id,
            kind: "evidence",
            status: "provenance",
            href: `/sources/${sourceId}`,
          },
          {
            source: id,
            target: relatedId,
            kind: "alias",
            status: "proposal",
            href: `/review?finding_id=${findingId}`,
          },
        ],
        truncated: false,
      };
    else if (path === `/api/concepts/${id}/provenance`)
      data = [
        {
          source,
          revision: {
            id,
            content_hash: "a".repeat(64),
            recorded_at: timestamp,
            publication,
          },
          segment: {
            id,
            text: `Intro${excerpt} Additional context: retain evidence for audits.`,
            heading: "Logging",
            location: { page: 4 },
          },
          context_after: {
            text: "Adjacent retained guidance explains audit review.",
            heading: "Review",
            location: { page: 4 },
          },
          occurrence: {
            id,
            concept_version_id: id,
            excerpt,
            start: 5,
            end: 5 + excerpt.length,
            extraction_metadata: { authority: "candidate" },
          },
        },
      ];
    else if (path === `/api/concepts/${id}/lineage`)
      data = { origins: [], dependents: [] };
    else if (path === "/api/issues")
      data = url.searchParams.get("severity") === "critical" ? [] : [issue];
    else if (path === `/api/issues/${id}`) data = { issue, events: [] };
    else if (path === "/api/reconciliation/findings") data = [finding];
    else if (path === "/api/reconciliation/backlog")
      data = {
        items:
          url.searchParams.get("status") === "pending"
            ? []
            : [{ finding, decision: { id, outcome: "deferred" } }],
        total: url.searchParams.get("status") === "pending" ? 0 : 1,
        next_cursor: null,
      };
    else if (path === `/api/reconciliation/findings/${findingId}`)
      data = finding;
    else if (path === "/api/reconciliation/decisions")
      data = url.searchParams.has("finding_id")
        ? [
            {
              id,
              finding_id: findingId,
              outcome: "deferred",
              actor: "Fixture reviewer",
              rationale: "More evidence required.",
              recorded_at: timestamp,
            },
          ]
        : [];
    else if (path === "/api/pipeline/status")
      data = {
        run: execution,
        executions: ["indexing", "enrichment", "issues"].map((stage) => ({
          ...execution,
          kind: "stage",
          processor_id: stage,
        })),
      };
    else if (path === "/api/scans")
      data = [
        { ...execution, discovered: 4, processed: 3, skipped: 1, failed: 0 },
      ];
    else if (path === "/api/statistics")
      data = {
        task_count: 3,
        failures: 0,
        input_tokens: 0,
        output_tokens: 0,
        configured_cost: null,
        model_latency_ms: 0,
        validation_failures: 0,
      };
    else if (path === "/api/inspection/tables")
      data = [
        { name: "sources", count: 1 },
        { name: "source_revisions", count: 2 },
      ];
    else if (path === "/api/inspection/tables/sources")
      data = {
        name: "sources",
        columns: [
          { name: "id", type: "UUID", reference: null },
          { name: "payload", type: "JSONB", reference: null },
        ],
        rows: [{ id: sourceId, payload: source }],
        next_cursor: null,
        previews: true,
      };
    else if (path === "/api/inspection/tables/source_revisions")
      data = {
        name: "source_revisions",
        columns: [
          { name: "id", type: "UUID", reference: null },
          { name: "source_id", type: "UUID", reference: "sources" },
        ],
        rows: [
          {
            id: url.searchParams.has("after") ? relatedId : id,
            source_id: sourceId,
          },
        ],
        next_cursor: url.searchParams.has("after") ? null : id,
        previews: true,
      };
    else if (path === "/api/inspection/logs")
      data = {
        events: [
          {
            timestamp,
            event: "source_failed",
            process: "123",
            fields: { error_type: "ValueError", source_id: sourceId },
          },
        ],
        files_read: 1,
        truncated: true,
      };
    else if (path === "/api/benchmarks/controls/mcsb-v2:NS-6")
      data = {
        control: {
          control: {
            control_id: "NS-6",
            title: "Protect application traffic",
            security_principle: "Protect web applications and APIs.",
            azure_guidance: "Use a suitable firewall.",
            generic_guidance: null,
            implementation_options: ["Gateway", "Front door"],
            implementation_options_are_alternatives: true,
            subcontrols: [],
            publication: { ...publication, publication_status: "unknown" },
            evidence: {
              excerpt: "Exact published control.",
              source_url: "https://example.com/control",
            },
          },
          mappings: [
            {
              reference: {
                framework: "NIST",
                control_id: "SC-7",
                resolution: "resolved",
                coverage: "unknown",
                evidence: {
                  excerpt: "Published mapping: SC-7",
                  source_url: "https://example.com/mapping",
                },
                resolved_concept_ids: [id],
              },
            },
          ],
          policies: [
            {
              reference: {
                name: "Example actual policy",
                definition_id: id,
                url: "https://example.com/policy",
              },
              definition: { name: "Example actual policy", version: "1.0.0" },
              version_matches_reference: null,
            },
          ],
        },
        edition: {
          edition: "mcsb-v2",
          publication: { ...publication, publication_status: "preview" },
        },
        concept: null,
      };
    else if (path === "/api/benchmarks/controls/mcsb-v2:NS-6/lineage")
      data = {
        published_edition_lineage: [
          {
            predecessor: "mcsb-v1",
            successor: "mcsb-v2",
            evidence: {
              excerpt: "This edition supersedes the earlier edition.",
              source_url: "https://example.com/succession",
            },
          },
        ],
        semantic_proposals: [],
      };
    else if (path === "/api/benchmarks/compare")
      data = {
        changes: [
          {
            old: {
              control: { control_id: "NS-6", title: "Protect applications" },
            },
            new: {
              control: {
                control_id: "NS-6",
                title: "Protect applications and APIs",
              },
            },
            lineage: "updated_version",
            proposed_change: "MINOR",
            changed_fields: ["security_principle"],
            version_note: "No numeric baseline established.",
            guidance_diff: "",
          },
        ],
        added_control_ids: ["NS-7"],
        removed_control_ids: [],
        model_calls: 0,
        notes: ["All semantic transitions require review."],
      };
    else
      return route.fulfill({
        status: 404,
        json: { detail: "No fixture for this API path" },
      });
    await route.fulfill({ json: data });
  });
}

test("ontology shows read-only definitions, relations and linked scope constraints", async ({
  page,
}) => {
  await fixture(page);
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (
      new URL(request.url()).pathname === "/api/ontology" &&
      request.method() !== "GET"
    )
      mutations.push(request.method());
  });
  await page.goto("/ontology");
  await expect(
    page.getByRole("heading", { name: "Ontology", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Concepts", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Semantic relations" }),
  ).toBeVisible();
  await expect(page.locator("#TechnicalRequirement")).toContainText(
    ontology.terms[0].definition,
  );
  await expect(page.locator("#supports")).toContainText(
    ontology.terms[2].definition,
  );
  await expect(page.locator("#subScopeOf")).toContainText("transitive");
  await expect(
    page
      .locator("#subScopeOf")
      .getByRole("link", { name: "Organizational scope" })
      .first(),
  ).toHaveAttribute("href", "/ontology#OrganizationalScope");
  await page.locator("#platformName summary").click();
  await expect(page.locator("#platformName")).toContainText(
    namespace + "platformName",
  );
  await expect(
    page.locator(
      "main textarea, main [contenteditable=true], main button[type=submit]",
    ),
  ).toHaveCount(0);

  await page.getByRole("searchbox", { name: "Search ontology" }).fill("vendor");
  await expect(page.locator("#TechnicalRequirement")).toBeVisible();
  await expect(page.locator("#supports")).toHaveCount(0);
  await page
    .getByRole("searchbox", { name: "Search ontology" })
    .fill("not-an-ontology-term");
  await expect(page.getByText("No matching ontology terms")).toBeVisible();
  expect(mutations).toEqual([]);
});

test("ontology supports deep links, themes and escaped definitions", async ({
  page,
}) => {
  await fixture(page);
  await page.route("**/api/ontology", async (route) => {
    const data = structuredClone(ontology);
    data.terms[0].definition = "<img src=x onerror=alert('not markup')>";
    await route.fulfill({ json: data });
  });
  await page.goto("/ontology#TechnicalRequirement");
  const term = page.locator("#TechnicalRequirement");
  await expect(term).toBeInViewport();
  await expect(term).toContainText("<img src=x");
  await expect(term.locator("img")).toHaveCount(0);
  const before = await term.evaluate(
    (element) => getComputedStyle(element).backgroundColor,
  );
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await expect(term).not.toHaveCSS("background-color", before);
});

test("ontology request failure is explicit and retryable", async ({ page }) => {
  await fixture(page);
  let fail = true;
  await page.route("**/api/ontology", async (route) => {
    if (fail)
      await route.fulfill({ status: 503, json: { detail: "Unavailable" } });
    else await route.fulfill({ json: ontology });
  });
  await page.goto("/ontology");
  await expect(page.getByRole("alert")).toContainText(
    "No results have been substituted",
  );
  fail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.locator("#TechnicalRequirement")).toBeVisible();
});

test.beforeEach(async ({ page }) => {
  await fixture(page);
});

test("architect workflow leads from shared language to scoped decisions", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Consolidate architecture standards." }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "Workspace navigation" })
      .getByRole("link", { name: "Shared language" }),
  ).toHaveAttribute("href", "/ontology");
  const workflow = page.getByRole("list", {
    name: "Standards consolidation workflow",
  });
  await expect(workflow.getByRole("link")).toHaveCount(4);
  for (const [index, href] of [
    "/sources",
    "/knowledge?level=",
    "/explore",
    "/review",
  ].entries())
    await expect(workflow.getByRole("link").nth(index)).toHaveAttribute(
      "href",
      href,
    );
  await expect(
    page.getByRole("heading", { name: "Needs your attention" }),
  ).toBeVisible();
  await expect(
    page.getByText("Unresolved Duplicate", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "View queue", exact: true }),
  ).toHaveAttribute("href", "/review?view=issues");
  await expect(page.getByText("Indexing", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
  await page.getByRole("link", { name: "Start with shared language" }).click();
  await expect(
    page.getByRole("region", { name: "Company intent" }),
  ).toContainText("Strategic goal");
  await expect(
    page.getByRole("region", { name: "Company intent" }),
  ).toContainText("Strategic requirement");
  await expect(
    page.getByRole("region", {
      name: "Classification context, not requirement candidates",
    }),
  ).toContainText("Organizational scope");
  await expect(
    page.getByRole("region", {
      name: "Classification context, not requirement candidates",
    }),
  ).not.toContainText("Strategic goal");
});

test("empty workspace guides ingestion without claiming completed review", async ({
  page,
}) => {
  await page.route("**/api/source-groups", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route("**/api/issues?**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/pipeline/status", (route) =>
    route.fulfill({ json: null }),
  );
  await page.goto("/");
  await expect(
    page.getByText("No open issues recorded", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/This is not evidence of coverage or approval/),
  ).toBeVisible();
  await page
    .getByRole("list", { name: "Standards consolidation workflow" })
    .getByRole("link")
    .first()
    .click();
  await expect(
    page.getByText("No sources registered", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/Configure source directories or manifests/),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "Ingestion & processing", exact: true })
    .last()
    .click();
  await expect(page).toHaveURL(/\/pipeline$/);
});

test("source search distinguishes no matches from an empty registry", async ({
  page,
}) => {
  await page.goto("/sources");
  await page
    .getByRole("searchbox", { name: "Search sources" })
    .fill("no-such-source");
  await expect(
    page.getByText("No matching sources", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("No sources registered", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("searchbox", { name: "Search sources" }).fill("");
  await page.getByRole("link", { name: source.item.name, exact: true }).click();
  await page.getByText("Source curation checks", { exact: true }).click();
  await expect(
    page.getByText(/guidance, not a saved curation or approval status/),
  ).toBeVisible();
});

test("search, exact evidence and scoped authority remain distinct", async ({
  page,
}) => {
  await page.goto("/knowledge");
  await page.getByRole("searchbox", { name: "Search knowledge" }).fill("audit");
  await page.getByRole("link", { name: statement, exact: true }).click();
  await expect(page.getByLabel("Classification definition")).toContainText(
    ontology.terms[0].definition,
  );
  await expect(
    page.getByLabel("Classification definition").getByRole("link"),
  ).toHaveAttribute("href", "/ontology#TechnicalRequirement");
  await expect(
    page.getByRole("group", {
      name: "Navigable evidence and relationship map",
    }),
  ).toHaveCount(0);
  await expect(page.locator("blockquote")).toHaveText(excerpt);
  await expect(page.locator("blockquote em")).toHaveCount(0);
  await expect(page.locator(".source-context")).toContainText(
    "Additional context: retain evidence for audits.",
  );
  await expect(page.locator(".source-context mark")).toHaveText(excerpt);
  await expect(page.locator(".source-context")).toContainText(
    "Adjacent retained guidance explains audit review.",
  );
  await expect(page.getByText("Exact segment offsets 5–")).toBeVisible();
  await page
    .getByRole("tab", { name: "Visual validation", exact: true })
    .click();
  await expect(
    page.getByRole("group", {
      name: "Navigable evidence and relationship map",
    }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Source evidence", exact: true }).click();
  await expect(page.locator("blockquote")).toHaveText(excerpt);
  await page.getByRole("tab", { name: "Scope & governance" }).click();
  await expect(
    page.getByText("Governance: Approved", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Canonical: Not Canonical", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/Effective period: 2020-01-01 to 2099-12-31/),
  ).toBeVisible();
});

test("selected version never inherits another version's approval", async ({
  page,
}) => {
  await page.route("**/api/ui/config", (route) =>
    route.fulfill({ json: { governance_enabled: true, scan_running: false } }),
  );
  await page.route(`**/api/concepts/${id}`, (route) =>
    route.fulfill({
      json: {
        ...concept,
        versions: [
          ...concept.versions,
          { ...concept.versions[0], id: relatedId },
        ],
        authority_states: [
          { ...concept.authority_states[0], concept_version_id: relatedId },
        ],
      },
    }),
  );
  await page.goto(`/concepts/${id}?version_id=${id}`);
  await expect(page.locator("blockquote")).toHaveText(excerpt);
  await page.getByRole("tab", { name: "Scope & governance" }).click();
  await expect(
    page.getByText("No authority recorded", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Governance: Approved", { exact: true }),
  ).toHaveCount(0);
  await page
    .getByText("Propose scoped canonical adoption", { exact: true })
    .click();
  await expect(page.getByLabel("Version to propose")).toHaveValue(id);
  await expect(page.locator("#canonical-version option")).toHaveCount(2);
});

test("the default version does not borrow historical source evidence", async ({
  page,
}) => {
  await page.route(`**/api/concepts/${id}`, (route) =>
    route.fulfill({
      json: {
        ...concept,
        versions: [
          ...concept.versions,
          {
            ...concept.versions[0],
            id: relatedId,
            candidate: {
              ...concept.versions[0].candidate,
              statement: "A newer interpretation without retained evidence.",
            },
          },
        ],
      },
    }),
  );
  await page.goto(`/concepts/${id}`);
  await expect(page.locator(".concept-statement")).toHaveText(
    "A newer interpretation without retained evidence.",
  );
  await expect(
    page.getByText("No evidence recorded for this version", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("blockquote")).toHaveCount(0);
});

test("inline classification definitions stay canonical, escaped and retryable", async ({
  page,
}) => {
  const definition =
    "Canonical <img src=x onerror=alert('not markup')> definition.";
  let fail = true;
  await page.route("**/api/ontology", (route) => {
    const data = structuredClone(ontology);
    data.terms[0].definition = definition;
    return fail
      ? route.fulfill({ status: 503, json: { detail: "Unavailable" } })
      : route.fulfill({ json: data });
  });
  await page.goto(`/concepts/${id}`);
  const panel = page.getByLabel("Classification definition");
  await expect(panel.getByRole("alert")).toBeVisible();
  await expect(page.locator(".concept-statement")).toHaveText(statement);
  fail = false;
  await panel.getByRole("button", { name: "Try again" }).click();
  await expect(panel).toContainText(definition);
  await expect(panel.locator("img")).toHaveCount(0);
});

test("source filtering keeps the evidenced assertion and resets pagination", async ({
  page,
}) => {
  const phrase = "Teams must retain audit logs for investigations.";
  const own = {
    ...concept.versions[0],
    assertion: {
      text: phrase,
      uncertainty: null,
      model_id: "synthetic",
      prompt_version: "v1",
      occurrence_id: id,
      source_id: sourceId,
    },
  };
  const item = {
    ...concept,
    matching_version_id: id,
    versions: [
      own,
      {
        ...concept.versions[0],
        id: relatedId,
        candidate: {
          ...concept.versions[0].candidate,
          statement: "An unrelated latest version.",
        },
      },
    ],
    sources: [
      {
        id: sourceId,
        name: source.item.name,
        publisher: "Example publisher",
        location: source.item.location,
        version_ids: [id],
        evidence_counts: { [id]: 1 },
      },
    ],
  };
  const requests: URL[] = [];
  await page.route("**/api/concepts?**", (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    return route.fulfill({
      json: Array.from({ length: 50 }, (_, index) => ({
        ...item,
        id: `${String(index + 1).padStart(8, "0")}-1111-4111-8111-111111111111`,
      })),
    });
  });
  await page.goto("/knowledge");
  await expect(
    page.getByRole("link", { name: phrase, exact: true }),
  ).toHaveCount(50);
  await expect(
    page.getByText("An unrelated latest version.", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect
    .poll(() => requests.at(-1)?.searchParams.has("after"))
    .toBe(true);
  await page
    .getByRole("combobox", { name: "Source", exact: true })
    .selectOption(sourceId);
  await expect
    .poll(() => requests.at(-1)?.searchParams.get("source_id"))
    .toBe(sourceId);
  expect(requests.at(-1)?.searchParams.has("after")).toBe(false);
  await expect(
    page.getByText("Page 1 · 50 records on this page"),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: phrase, exact: true }).first(),
  ).toHaveAttribute(
    "href",
    new RegExp(`version_id=${id}&source_id=${sourceId}`),
  );
  await page.reload();
  await expect(
    page.getByRole("combobox", { name: "Source", exact: true }),
  ).toHaveValue(sourceId);
  await page
    .getByRole("combobox", { name: "Source", exact: true })
    .selectOption("");
  await expect
    .poll(() => requests.at(-1)?.searchParams.has("source_id"))
    .toBe(false);
});

test("logical editions consolidate documents and preserve exact-source navigation", async ({
  page,
}) => {
  const secondDocument = {
    ...source,
    item: { ...source.item, id: relatedId, name: "Example policy definitions" },
  };
  const group = {
    ...sourceGroup,
    id: findingId,
    name: "Example security benchmark v1",
    edition: "example-v1",
    documents: [source, secondDocument],
  };
  const next = {
    ...sourceGroup,
    id: "55555555-5555-4555-8555-555555555555",
    name: "Example security benchmark v2",
    edition: "example-v2",
    documents: [
      {
        ...source,
        item: { ...source.item, id: "66666666-6666-4666-8666-666666666666" },
      },
    ],
  };
  const phrase = "Teams must retain audit logs for investigations.";
  const version = {
    ...concept.versions[0],
    assertion: { text: phrase, source_id: sourceId },
  };
  const item = {
    ...concept,
    matching_version_id: id,
    versions: [version],
    sources: [
      {
        id: sourceId,
        name: source.item.name,
        publisher: "Example publisher",
        version_ids: [id],
        evidence_counts: { [id]: 1 },
      },
    ],
  };
  await page.route("**/api/source-groups", (route) =>
    route.fulfill({ json: [group, next] }),
  );
  await page.route(`**/api/sources/${findingId}?*`, (route) =>
    route.fulfill({
      json: {
        source,
        group,
        revisions: 2,
        segments: 2,
        versions: 1,
        occurrences: 2,
        items: [{ concept_id: id, version, evidence_count: 2 }],
        next_cursor: null,
      },
    }),
  );
  await page.route("**/api/concepts?**", (route) =>
    route.fulfill({ json: [item] }),
  );
  await page.route(`**/api/concepts/${id}`, (route) =>
    route.fulfill({ json: item }),
  );
  await page.goto("/sources");
  await expect(
    page.getByRole("link", { name: group.name, exact: true }),
  ).toHaveCount(1);
  await expect(
    page.getByRole("link", { name: next.name, exact: true }),
  ).toHaveCount(1);
  await expect(page.getByText("2 documents", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: group.name, exact: true }).click();
  await expect(
    page.getByRole("heading", { name: group.name, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: secondDocument.item.name, exact: true }),
  ).toHaveAttribute("href", `/sources/${relatedId}`);
  await expect(
    page.getByRole("link", { name: phrase, exact: true }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "Filter knowledge by this source" })
    .click();
  await expect(
    page.getByRole("combobox", { name: "Source", exact: true }),
  ).toHaveValue(findingId);
  await expect(
    page.getByRole("combobox", { name: "Evidence document" }),
  ).toHaveValue("");
  await page.getByRole("link", { name: phrase, exact: true }).click();
  await expect(page.locator(".concept-statement").first()).toHaveText(phrase);
  await expect(page.locator("blockquote")).toHaveText(excerpt);
  await page.getByRole("link", { name: "Interpretation library" }).click();
  await page
    .getByRole("combobox", { name: "Evidence document" })
    .selectOption(sourceId);
  await expect(page).toHaveURL(new RegExp(`source_id=${sourceId}`));
  await page.reload();
  await expect(
    page.getByRole("combobox", { name: "Source", exact: true }),
  ).toHaveValue(findingId);
  await expect(
    page.getByRole("combobox", { name: "Evidence document" }),
  ).toHaveValue(sourceId);
  await page
    .getByRole("combobox", { name: "Evidence document" })
    .selectOption("");
  await expect(page).toHaveURL(new RegExp(`source_id=${findingId}`));
});

test("logical-source filters reject phrasing cached against another edition", async ({
  page,
}) => {
  const group = {
    ...sourceGroup,
    id: findingId,
    edition: "example-v1",
  };
  const version = {
    ...concept.versions[0],
    assertion: {
      text: "A summary from another edition.",
      source_id: relatedId,
    },
  };
  const item = { ...concept, versions: [version] };
  await page.route("**/api/source-groups", (route) =>
    route.fulfill({ json: [group] }),
  );
  await page.route("**/api/concepts?**", (route) =>
    route.fulfill({ json: [item] }),
  );
  await page.route(`**/api/concepts/${id}`, (route) =>
    route.fulfill({ json: item }),
  );
  await page.goto(`/knowledge?source_id=${findingId}`);
  await expect(
    page.getByRole("link", { name: statement, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("A summary from another edition.", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: statement, exact: true }).click();
  await expect(page.locator(".concept-statement").first()).toHaveText(
    statement,
  );
  await expect(page.locator("blockquote")).toHaveText(excerpt);
});

test("quote context respects Unicode offsets and renders untrusted markup as text", async ({
  page,
}) => {
  await page.route(`**/api/concepts/${id}/provenance`, (route) =>
    route.fulfill({
      json: [
        {
          source,
          revision: {
            id,
            content_hash: "a".repeat(64),
            recorded_at: timestamp,
            publication,
          },
          segment: {
            id,
            text: `😀 ${excerpt} Subsequent source context.`,
            heading: "Logging",
            location: { page: 4 },
          },
          occurrence: {
            id,
            concept_version_id: id,
            excerpt,
            start: 2,
            end: 2 + excerpt.length,
            extraction_metadata: {},
          },
        },
      ],
    }),
  );
  await page.goto(`/concepts/${id}`);
  await expect(page.locator(".source-context mark")).toHaveText(excerpt);
  await expect(page.locator(".source-context")).toContainText(
    "Subsequent source context.",
  );
  await expect(page.locator(".source-context em")).toHaveCount(0);
});

test("issue filters and evidence drill-down do not expose write controls", async ({
  page,
}) => {
  await page.goto("/review");
  await page.getByRole("tab", { name: "Issues", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Issue severity" })
    .selectOption("critical");
  await expect(page.getByText("Nothing to show yet")).toBeVisible();
  await page.getByRole("combobox", { name: "Issue severity" }).selectOption("");
  const inspect = page.getByRole("button", {
    name: "Inspect issue Unresolved Duplicate",
  });
  await inspect.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByText(issue.explanation, { exact: true }).last(),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Record issue review" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Inspect supporting finding 1" }),
  ).toHaveAttribute("href", `/review?finding_id=${findingId}`);
  await page.keyboard.press("Escape");
  await expect(inspect).toBeFocused();
});

test("proposal deep links preserve the previous-decision guard", async ({
  page,
}) => {
  await fixture(page, true);
  await page.goto(`/review?finding_id=${findingId}`);
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByText("More evidence required.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator('form[action="/review/decisions"] input[name="previous_id"]'),
  ).toHaveValue(id);
  await expect(
    page.getByRole("button", { name: "Record decision", exact: true }),
  ).toBeEnabled();
  await expect(page.getByLabel("Rationale", { exact: true })).toHaveAttribute(
    "required",
    "",
  );
});

test("source details retain unknown versions and publisher attribution", async ({
  page,
}) => {
  await page.goto("/sources");
  await page
    .getByRole("button", {
      name: "Inspect source Example architecture standard",
    })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByRole("dialog").getByText("Example publisher", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Unknown", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Open publisher location" }),
  ).toHaveAttribute("href", source.item.location);
});

test("read failures are not disguised as empty data and can be retried", async ({
  page,
}) => {
  await page.route("**/api/concepts?**", (route) =>
    route.fulfill({ status: 503, json: { detail: "Unavailable" } }),
  );
  await page.goto("/knowledge");
  await expect(page.getByRole("alert")).toContainText(
    "Could not load this information",
  );
  await expect(page.getByText("Nothing to show yet")).toHaveCount(0);
  await page.unroute("**/api/concepts?**");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("link", { name: statement, exact: true }),
  ).toBeVisible();
});

test("dark theme persists across desktop navigation", async ({ page }) => {
  await page.goto("/?scoutTheme=light");
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page
    .getByRole("link", { name: "Interpretations", exact: true })
    .click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(
    page.getByRole("link", { name: statement, exact: true }),
  ).toBeVisible();
});

test("Bold Wikipedia tokens style primitives and custom views in both modes with a local font", async ({
  page,
}) => {
  await page.goto("/?scoutTheme=light");
  await page.evaluate(() => document.fonts.ready);
  const backgrounds: string[] = [];
  for (const mode of ["light", "dark"]) {
    await expect(page.locator("html")).toHaveAttribute("data-theme", mode);
    const styles = await page.evaluate(() => {
      const probe = document.createElement("div");
      document.body.append(probe);
      function color(token: string) {
        probe.style.backgroundColor = `var(${token})`;
        return getComputedStyle(probe).backgroundColor;
      }
      probe.style.borderRadius = "calc(var(--radius) + 4px)";
      const expectedCardRadius = getComputedStyle(probe).borderRadius;
      probe.style.borderRadius = "var(--radius)";
      const expectedRadius = getComputedStyle(probe).borderRadius;
      const card = getComputedStyle(document.querySelector(".metric")!);
      const sidebar = getComputedStyle(document.querySelector(".sidebar")!);
      const active = getComputedStyle(
        document.querySelector('.sidebar a[aria-current="page"]')!,
      );
      const result = {
        body: getComputedStyle(document.body).backgroundColor,
        expectedBody: color("--background"),
        card: card.backgroundColor,
        expectedCard: color("--card"),
        sidebar: sidebar.backgroundColor,
        expectedSidebar: color("--sidebar"),
        expectedAction: color("--primary"),
        cardRadius: card.borderRadius,
        expectedCardRadius,
        navigationRadius: active.borderRadius,
        expectedRadius,
        shadow: card.boxShadow,
        font: getComputedStyle(document.body).fontFamily,
        loadedInter: [...document.fonts].some(
          (font) =>
            font.family.replaceAll('"', "") === "Inter" &&
            font.status === "loaded",
        ),
      };
      probe.remove();
      return result;
    });
    expect(styles.body).toBe(styles.expectedBody);
    expect(styles.card).toBe(styles.expectedCard);
    expect(styles.sidebar).toBe(styles.expectedSidebar);
    await expect(
      page.getByRole("link", {
        name: "Start with shared language",
        exact: true,
      }),
    ).toHaveCSS("background-color", styles.expectedAction);
    expect(styles.cardRadius).toBe(styles.expectedCardRadius);
    expect(styles.navigationRadius).toBe(styles.expectedRadius);
    expect(styles.shadow).not.toBe("none");
    expect(styles.font).toContain("Inter");
    expect(styles.loadedInter).toBe(true);
    backgrounds.push(styles.body);
    if (mode === "light")
      await page.getByRole("button", { name: "Use dark theme" }).click();
  }
  expect(backgrounds[0]).not.toBe(backgrounds[1]);
  const fontRequests = await page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((entry) => entry.name)
      .filter((name) => name.includes(".woff2")),
  );
  expect(fontRequests.length).toBeGreaterThan(0);
  expect(
    fontRequests.every(
      (url) => new URL(url).origin === new URL(page.url()).origin,
    ),
  ).toBe(true);
});

test("pipeline distinguishes indexing scans from all three phases", async ({
  page,
}) => {
  await page.goto("/pipeline");
  await expect(
    page.getByRole("heading", { name: "Every phase, accounted for." }),
  ).toBeVisible();
  await expect(page.getByText("PHASE 3", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Reported priced cost", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Unknown", { exact: true })).toBeVisible();
  await expect(
    page.getByText(/This starts source indexing only/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Scan configured public manifest" }),
  ).toHaveAttribute("name", "public_manifest");
});

test("known structured semantic versions retain prerelease and build identifiers", async ({
  page,
}) => {
  const version = {
    major: 2,
    minor: 3,
    patch: 4,
    prerelease: ["rc", "1"],
    build: ["audit"],
  };
  await page.route(`**/api/concepts/${id}`, (route) =>
    route.fulfill({
      json: {
        ...concept,
        versions: [{ ...concept.versions[0], semantic_version: version }],
      },
    }),
  );
  await page.goto(`/concepts/${id}`);
  await page.getByRole("tab", { name: "Versions & relationships" }).click();
  await expect(
    page.getByText("SemVer: 2.3.4-rc.1+audit", { exact: true }),
  ).toBeVisible();
  await page.route("**/api/source-groups", (route) =>
    route.fulfill({
      json: [
        {
          ...sourceGroup,
          documents: [
            {
              ...source,
              publication: { ...publication, semantic_version: version },
            },
          ],
        },
      ],
    }),
  );
  await page.goto("/sources");
  await page
    .getByRole("button", {
      name: "Inspect source Example architecture standard",
    })
    .click();
  await expect(
    page.getByText("2.3.4-rc.1+audit", { exact: true }),
  ).toBeVisible();
});

test("knowledge paging uses server cursors and resets on a new search", async ({
  page,
}) => {
  const first = Array.from({ length: 50 }, (_, index) => ({
    ...concept,
    id: `${String(index + 1).padStart(8, "0")}-1111-4111-8111-111111111111`,
  }));
  const after = first.at(-1)!.id;
  const requests: URL[] = [];
  await page.route("**/api/concepts?**", async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    await route.fulfill({
      json: url.searchParams.has("after")
        ? [
            {
              ...concept,
              versions: [
                {
                  ...concept.versions[0],
                  candidate: {
                    ...concept.versions[0].candidate,
                    statement: "Later concept from the next server page.",
                  },
                },
              ],
            },
          ]
        : first,
    });
  });
  await page.goto("/knowledge");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(
    page.getByRole("link", {
      name: "Later concept from the next server page.",
      exact: true,
    }),
  ).toBeVisible();
  expect(requests.at(-1)?.searchParams.get("after")).toBe(after);
  await page
    .getByRole("searchbox", { name: "Search knowledge" })
    .fill("new search");
  await expect(
    page.getByText("Page 1 · 50 records on this page"),
  ).toBeVisible();
  expect(requests.at(-1)?.searchParams.get("q")).toBe("new search");
  expect(requests.at(-1)?.searchParams.has("after")).toBe(false);
});

test("issue deep links work even when the filtered queue is empty", async ({
  page,
}) => {
  await page.route("**/api/issues?**", (route) => route.fulfill({ json: [] }));
  await page.goto(`/review?issue_id=${id}`);
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByRole("dialog").getByText(issue.explanation, { exact: true }),
  ).toBeVisible();
});

test("benchmark mappings, alternatives and lineage do not imply authority", async ({
  page,
}) => {
  await page.goto("/benchmarks");
  await expect(
    page.getByText("Edition maturity: Preview", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Source maturity: Unknown", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "Unknown", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Guidance & alternatives" }).click();
  await expect(
    page.getByText("These are alternatives, not cumulative requirements."),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Lineage", exact: true }).click();
  await expect(page.locator("blockquote")).toHaveText(
    "This edition supersedes the earlier edition.",
  );
  await page
    .getByRole("button", { name: "Compare editions", exact: true })
    .click();
  await expect(page.getByText("0 model calls", { exact: true })).toBeVisible();
  await expect(
    page.getByText("All semantic transitions require review.", { exact: true }),
  ).toBeVisible();
});

test("reference sources drill into source-bound versions and exact evidence", async ({
  page,
}) => {
  await page.route(`**/api/concepts/${id}`, (route) =>
    route.fulfill({
      json: {
        ...concept,
        versions: [
          ...concept.versions,
          {
            ...concept.versions[0],
            id: relatedId,
            candidate: {
              ...concept.versions[0].candidate,
              statement: "A later interpretation from elsewhere.",
            },
          },
        ],
      },
    }),
  );
  await page.goto("/sources");
  await expect(
    page.getByRole("navigation", { name: "Consolidate navigation" }),
  ).toContainText("Sources");
  await page.getByRole("link", { name: source.item.name, exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "Extracted interpretations",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("link", { name: statement, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`version_id=${id}`));
  await expect(page.locator(".concept-statement")).toHaveText(statement);
  await expect(page.locator("blockquote")).toHaveText(excerpt);
  await page.getByRole("link", { name: source.item.name, exact: true }).click();
  await expect(
    page.getByRole("heading", { name: source.item.name, exact: true }),
  ).toBeVisible();
});

test("visual exploration recenters connected interpretations and separates proposal links", async ({
  page,
}) => {
  await page.goto(`/explore?concept=${id}`);
  await expect(
    page.getByRole("group", {
      name: "Navigable evidence and relationship map",
    }),
  ).toBeVisible();
  await expect(page.locator(".graph-edge.is-proposal")).toHaveCount(0);
  await page.getByRole("button", { name: "Show review proposals" }).click();
  await expect(page.locator(".graph-edge.is-proposal")).toHaveCount(1);
  await expect(
    page.getByRole("link", { name: "Inspect Alias Proposal link" }),
  ).toHaveAttribute("href", `/review?finding_id=${findingId}`);
  await page
    .getByRole("link", { name: "Explore An associated security objective." })
    .click();
  await expect(page).toHaveURL(new RegExp(`concept=${relatedId}`));
  await expect(
    page.getByRole("link", {
      name: "Inspect interpretation An associated security objective.",
    }),
  ).toBeVisible();
});

test("visual validation requires an intentional starting point", async ({
  page,
}) => {
  await page.goto("/explore");
  await expect(page.locator(".graph-choices a")).toHaveCount(1);
  await expect(
    page.getByRole("group", {
      name: "Navigable evidence and relationship map",
    }),
  ).toHaveCount(0);
  await page.locator(".graph-choices a").click();
  await expect(page).toHaveURL(new RegExp(`concept=${id}`));
  await expect(
    page.getByRole("group", {
      name: "Navigable evidence and relationship map",
    }),
  ).toBeVisible();
});

test("knowledge levels separate platform implementation and persist the selection", async ({
  page,
}) => {
  const platform = {
    ...concept,
    levels: { [id]: "implementation" },
    versions: [
      {
        ...concept.versions[0],
        candidate: {
          ...concept.versions[0].candidate,
          statement: "Configure Azure Firewall for inspected traffic.",
          scope: { platform: "Azure" },
        },
      },
    ],
  };
  await page.route("**/api/concepts?**", (route) => {
    const level = new URL(route.request().url()).searchParams.get("level");
    return route.fulfill({
      json: level === "implementation" ? [platform] : [concept],
    });
  });
  await page.goto("/knowledge");
  const filter = page.getByRole("combobox", { name: "Architecture level" });
  await expect(filter).toHaveValue("top_level");
  await expect(page.locator(".statement-link")).toHaveText(statement);
  await filter.selectOption("implementation");
  await expect(page.locator(".statement-link")).toHaveText(
    "Configure Azure Firewall for inspected traffic.",
  );
  await expect(page.locator("tbody")).toContainText("Platform implementation");
  await expect(page).toHaveURL(/level=implementation/);
  await page.reload();
  await expect(filter).toHaveValue("implementation");
});

test("published control mappings link top-level guidance above implementation without invented alignment", async ({
  page,
}) => {
  await page.route(`**/api/concepts/${id}/graph`, (route) =>
    route.fulfill({
      json: {
        root: id,
        nodes: [
          {
            id,
            label: "Azure mcsb-v2:NS-6: Protect web applications.",
            kind: "requirement",
            level: "implementation",
            href: `/concepts/${id}`,
          },
          {
            id: sourceId,
            label: "NIST SP800-53 Rev.5 / SC-7",
            kind: "framework_control",
            level: "top_level",
            href: `/sources/${sourceId}`,
          },
          {
            id: relatedId,
            label: "A company objective awaiting alignment.",
            kind: "objective",
            level: "top_level",
            href: `/concepts/${relatedId}`,
          },
        ],
        edges: [
          {
            source: id,
            target: sourceId,
            kind: "MAPS_TO",
            status: "published",
            href: "/benchmarks?reference=mcsb-v2:NS-6",
            evidence: {
              excerpt: "NIST SP800-53 Rev.5: SC-7",
              source_url: "https://example.com/benchmark",
            },
            note: "Published control-level mapping, not company alignment.",
          },
          {
            source: id,
            target: relatedId,
            kind: "SUPPORTS",
            status: "proposal",
            href: `/review?finding_id=${findingId}`,
          },
        ],
        truncated: false,
      },
    }),
  );
  await page.goto(`/explore?concept=${id}`);
  const native = page.getByRole("link", {
    name: "Browse native control NIST SP800-53 Rev.5 / SC-7",
  });
  const platform = page.getByRole("link", {
    name: "Inspect interpretation Azure mcsb-v2:NS-6: Protect web applications.",
  });
  await expect(native).toBeVisible();
  expect(Number(await native.locator("rect").getAttribute("y"))).toBeLessThan(
    Number(await platform.locator("rect").getAttribute("y")),
  );
  await expect(
    page.getByRole("link", {
      name: /Explore A company objective awaiting alignment/,
    }),
  ).toHaveCount(0);
  await page.getByText("Link basis & evidence", { exact: true }).click();
  await expect(page.locator(".graph-record blockquote")).toHaveText(
    "NIST SP800-53 Rev.5: SC-7",
  );
  await expect(native).toHaveAttribute("href", `/sources/${sourceId}`);
});

test("work backlog is separate from the reference reconciliation ledger", async ({
  page,
}) => {
  await page.goto("/review");
  await expect(
    page.getByRole("heading", { name: "Active reconciliation backlog" }),
  ).toBeVisible();
  await expect(page.getByText("1 outstanding proposals")).toBeVisible();
  await page
    .getByRole("combobox", { name: "Backlog decision state" })
    .selectOption("pending");
  await expect(page.getByText("0 outstanding proposals")).toBeVisible();
  await expect(page.getByText("Nothing to show yet")).toBeVisible();
  await page
    .getByRole("main")
    .getByRole("link", { name: "Reconciliation log", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Recorded proposals" }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "Backlog decision state" }),
  ).toHaveCount(0);
  await page
    .getByRole("tab", { name: "Decision history", exact: true })
    .click();
  await expect(
    page.getByText("No decisions recorded", { exact: true }),
  ).toBeVisible();
});

test("data inspector follows foreign keys and respects terminal cursor pages", async ({
  page,
}) => {
  await page.goto("/data?table=source_revisions");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText(relatedId, { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Next", exact: true }),
  ).toBeDisabled();
  await page.getByRole("link", { name: sourceId, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`table=sources&record=${sourceId}`));
  await page.getByText("Inspect JSON preview", { exact: true }).click();
  await expect(page.locator("pre")).toContainText(source.item.name);
  await expect(page.getByRole("button", { name: /delete|save/i })).toHaveCount(
    0,
  );
});

test("operational log tails are inspectable and searchable", async ({
  page,
}) => {
  await page.goto("/logs");
  await expect(page.getByText("Source Failed", { exact: true })).toBeVisible();
  await expect(page.getByText(/A bounded recent tail is shown/)).toBeVisible();
  await page.getByText("Inspect event fields", { exact: true }).click();
  await expect(page.locator("pre")).toContainText("ValueError");
  await page
    .getByRole("searchbox", { name: "Search loaded log events" })
    .fill("not-present");
  await expect(page.getByText("Nothing to show yet")).toBeVisible();
});
