import { test, expect, type Page } from "@playwright/test";

const id = "11111111-1111-4111-8111-111111111111";
const findingId = "22222222-2222-4222-8222-222222222222";
const relatedId = "33333333-3333-4333-8333-333333333333";
const sourceId = "44444444-4444-4444-8444-444444444444";
const architectureId = "99999999-9999-4999-8999-999999999999";
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
const concept = {
  id,
  levels: { [id]: "platform_agnostic" },
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
  recorded_at: timestamp,
};
async function fixture(page: Page, governance = false) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = decodeURIComponent(url.pathname);
    let data: unknown;
    if (path === "/api/ui/config") data = { governance_enabled: governance };
    else if (path === "/api/ontology") data = ontology;
    else if (path === "/api/sources") data = [source];
    else if (path === "/api/architectures") data = [];
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
      path === `/api/concepts/${id}/traceability` ||
      path === `/api/concepts/${relatedId}/graph`
    )
      data = {
        root: path.includes(relatedId) ? relatedId : id,
        nodes: [
          {
            id,
            label: statement,
            kind: "requirement",
            level: "platform_agnostic",
            href: `/concepts/${id}`,
          },
          {
            id: relatedId,
            label: "An associated security objective.",
            kind: "objective",
            level: "company",
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
            kind: "supports",
            status: "configured",
            href: `/concepts/${relatedId}`,
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

test("Mermaid diagram reflects declared ontology terms and constraints", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/ontology");
  await page.getByText("Show Mermaid diagram", { exact: true }).click();
  const diagram = page.getByRole("figure", {
    name: "OWL classes, properties and declared ontology constraints",
  });
  await expect(diagram.locator("svg")).toBeVisible();
  await expect(diagram).toContainText("Strategic goal");
  await expect(diagram).toContainText("supports");
  await expect(diagram).toContainText(/sub-scope of\s*\(transitive\)/);
  await expect(diagram).toContainText("xsd:string");
  await page.getByText("View Mermaid source", { exact: true }).click();
  const source = await page.locator(".ontology-mermaid-source").innerText();
  const scope = source.match(
    /(t\d+)\["«owl:Class» Organizational scope"\]/,
  )?.[1];
  const supports = source.match(/(t\d+)\["supports"\]/)?.[1];
  expect(scope).toBeTruthy();
  expect(supports).toBeTruthy();
  expect(source).toContain('subgraph Company_intent["Company intent"]');
  expect(source).toContain(
    'subgraph Platform-agnostic_architecture["Platform-agnostic architecture"]',
  );
  expect(source).toContain(
    'subgraph Platform-specific_implementation["Platform-specific implementation"]',
  );
  expect(source).toContain(`${scope} -->|"sub-scope of"| ${scope}`);
  expect(source).not.toContain(`${supports} -->`);
  expect(source).not.toContain("undefined");

  const node = diagram.locator(".node rect").first();
  const before = await node.evaluate(
    (element) => getComputedStyle(element).fill,
  );
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await expect(node).not.toHaveCSS("fill", before);
  await page.getByText("Hide Mermaid diagram", { exact: true }).click();
  await expect(diagram).toHaveCount(0);
});

test("Mermaid escapes ontology labels", async ({ page }) => {
  await fixture(page);
  await page.route("**/api/ontology", (route) => {
    const data = structuredClone(ontology);
    data.terms[0].label = 'Literal "quote" <img src=x onerror=alert(1)>';
    return route.fulfill({ json: data });
  });
  await page.goto("/ontology");
  await page.getByText("Show Mermaid diagram", { exact: true }).click();
  const diagram = page.getByRole("figure", {
    name: "OWL classes, properties and declared ontology constraints",
  });
  await expect(diagram.locator("svg")).toBeVisible();
  await expect(diagram).toContainText('Literal "quote"');
  await expect(diagram.locator("img, script")).toHaveCount(0);
});

test("Mermaid loading failures remain visible and retryable", async ({
  page,
}) => {
  await fixture(page);
  let fail = true;
  await page.route("**/node_modules/.vite/deps/mermaid.js?*", (route) =>
    fail ? route.abort() : route.continue(),
  );
  await page.goto("/ontology");
  await page.getByText("Show Mermaid diagram", { exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Could not render the Mermaid diagram",
  );
  await expect(page.locator("#TechnicalRequirement")).toContainText(
    ontology.terms[0].definition,
  );
  fail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.locator(".ontology-diagram svg")).toBeVisible();
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
  await expect(workflow.getByRole("link")).toHaveCount(6);
  for (const [index, href] of [
    "/sources",
    "/company",
    "/architecture",
    "/platforms",
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
      name: "Organizational applicability",
    }),
  ).toContainText("Organizational scope");
  await expect(
    page.getByRole("region", {
      name: "Organizational applicability",
    }),
  ).not.toContainText("Strategic goal");
});

test("empty workspace directs ingestion to the CLI without claiming completed review", async ({
  page,
}) => {
  await page.route("**/api/sources", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/issues?**", (route) => route.fulfill({ json: [] }));
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
    page.getByText(/Configure sources and run ingestion with the CLI/),
  ).toBeVisible();
  await expect(page.locator('a[href^="/pipeline"]')).toHaveCount(0);
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
  const traceability = page.getByRole("region", {
    name: "Traceability chain",
    exact: true,
  });
  await expect(traceability).toBeVisible();
  await expect(traceability).toContainText("Company intent");
  await expect(traceability).toContainText("An associated security objective.");
  await expect(traceability).toContainText(statement);
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
    page.getByRole("region", {
      name: "Full traceability chain",
      exact: true,
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
    route.fulfill({ json: { governance_enabled: true } }),
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

test("source filters reject phrasing cached against another source", async ({
  page,
}) => {
  const version = {
    ...concept.versions[0],
    assertion: {
      text: "A summary from another source.",
      source_id: relatedId,
    },
  };
  const item = { ...concept, versions: [version] };
  await page.route("**/api/concepts?**", (route) =>
    route.fulfill({ json: [item] }),
  );
  await page.route(`**/api/concepts/${id}`, (route) =>
    route.fulfill({ json: item }),
  );
  await page.goto(`/knowledge?source_id=${sourceId}`);
  await expect(
    page.getByRole("link", { name: statement, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("A summary from another source.", { exact: true }),
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
  await page.getByRole("link", { name: "Company intent", exact: true }).click();
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

test("workspace has no ingestion controls, monitoring links or requests", async ({
  page,
}) => {
  const requests: string[] = [];
  page.on("request", (request) =>
    requests.push(new URL(request.url()).pathname),
  );
  for (const path of [
    "/",
    "/sources",
    `/sources/${sourceId}`,
    "/reconciliation",
  ]) {
    await page.goto(path);
    await expect(page.locator('a[href^="/pipeline"]')).toHaveCount(0);
    await expect(page.locator('form[action="/scans"]')).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: "Ingestion & processing" }),
    ).toHaveCount(0);
    await expect(
      page.getByText("Latest pipeline", { exact: true }),
    ).toHaveCount(0);
  }
  await expect(
    page.getByRole("link", { name: "Issue review events" }),
  ).toBeVisible();
  await expect(
    page.locator('a[href="/data?table=pipeline_executions"]'),
  ).toHaveCount(0);
  expect(
    requests.filter((path) =>
      [
        "/api/scans",
        "/api/tasks",
        "/api/statistics",
        "/api/pipeline/status",
      ].includes(path),
    ),
  ).toEqual([]);
});

test("workspace has no benchmark navigation or specialized source requests", async ({
  page,
}) => {
  const requests: string[] = [];
  page.on("request", (request) =>
    requests.push(new URL(request.url()).pathname),
  );
  const pages = [
    {
      path: "/",
      ready: page
        .locator(".metric")
        .filter({ hasText: "Registered sources" })
        .getByText("1", { exact: true }),
    },
    {
      path: "/sources",
      ready: page.getByRole("link", { name: source.item.name, exact: true }),
    },
    {
      path: `/sources/${sourceId}`,
      ready: page.getByRole("heading", {
        name: "Extracted interpretations",
        exact: true,
      }),
    },
    {
      path: "/knowledge",
      ready: page.getByRole("link", { name: statement, exact: true }),
    },
    {
      path: `/explore?concept=${id}`,
      ready: page.getByRole("group", {
        name: "Navigable evidence and relationship map",
      }),
    },
  ];
  for (const { path, ready } of pages) {
    await page.goto(path);
    await expect(ready).toBeVisible();
    await expect(page.locator('a[href^="/benchmarks"]')).toHaveCount(0);
    await expect(
      page.getByRole("navigation").getByRole("link", { name: /benchmark/i }),
    ).toHaveCount(0);
  }
  expect(
    requests.filter(
      (path) =>
        path.startsWith("/api/benchmarks") ||
        path.startsWith("/api/source-groups"),
    ),
  ).toEqual([]);
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
  await page.route("**/api/sources", (route) =>
    route.fulfill({
      json: [
        {
          ...source,
          publication: { ...publication, semantic_version: version },
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

test("inferred provenance is visible by default but remains review-only", async ({
  page,
}) => {
  await page.route(`**/api/concepts/${id}/graph`, (route) =>
    route.fulfill({
      json: {
        root: id,
        nodes: [
          {
            id,
            label: statement,
            kind: "PlatformRequirement",
            href: `/concepts/${id}`,
            level: "platform_specific",
          },
          {
            id: relatedId,
            label: "Protect managed network boundaries.",
            kind: "TechnicalRequirement",
            href: `/concepts/${relatedId}`,
            level: "platform_agnostic",
          },
        ],
        edges: [
          {
            source: id,
            target: relatedId,
            kind: "refines",
            status: "inferred",
            href: `/review?finding_id=${findingId}`,
            note: "A grounded model selected the broader requirement.",
          },
        ],
        truncated: false,
      },
    }),
  );

  await page.goto(`/explore?concept=${id}`);

  await expect(page.locator(".graph-edge.is-proposal")).toHaveCount(1);
  await expect(
    page.getByRole("link", { name: "Inspect Refines Inferred link" }),
  ).toHaveAttribute("href", `/review?finding_id=${findingId}`);
  await expect(page.locator(".graph-links")).toContainText("Inferred");
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

test("knowledge workspaces separate company, architecture, and platform concepts", async ({
  page,
}) => {
  const company = {
    ...concept,
    levels: { [id]: "company" },
    versions: [
      {
        ...concept.versions[0],
        candidate: {
          ...concept.versions[0].candidate,
          semantic_type: "StrategicGoal",
          statement: "Become a resilient enterprise.",
          modality: "unspecified",
          goal_level: "enterprise",
        },
      },
    ],
  };
  const subordinate = {
    ...company,
    id: relatedId,
    versions: [
      {
        ...company.versions[0],
        id: findingId,
        concept_id: relatedId,
        candidate: {
          ...company.versions[0].candidate,
          statement: "Increase a business metric by 5%.",
          goal_level: "subordinate_target",
        },
      },
    ],
  };
  const platform = {
    ...concept,
    levels: { [id]: "platform_specific" },
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
      json:
        level === "platform_specific"
          ? [platform]
          : level === "company"
            ? [company, subordinate]
            : [concept],
    });
  });
  await page.goto("/company");
  await expect(
    page.getByRole("heading", { name: "Understand enterprise direction." }),
  ).toBeVisible();
  await expect(page.locator(".statement-link")).toHaveText(
    "Become a resilient enterprise.",
  );
  await expect(
    page.getByRole("link", { name: "Increase a business metric by 5%." }),
  ).toHaveCount(0);
  await page
    .getByRole("link", { name: "Platform-agnostic", exact: true })
    .click();
  await expect(page).toHaveURL(/\/architecture$/);
  await expect(page.locator(".statement-link")).toHaveText(statement);
  await page.getByRole("link", { name: "Platforms & controls" }).click();
  await expect(page).toHaveURL(/\/platforms$/);
  await expect(page.locator(".statement-link")).toHaveText(
    "Configure Azure Firewall for inspected traffic.",
  );
  await expect(page.locator("tbody")).toContainText("Platform-specific");
});

test("solution registry keeps canonical artefacts, requirements, and exceptions together", async ({
  page,
}) => {
  const revision = {
    id: findingId,
    architecture_id: architectureId,
    name: "Public files",
    description: "Publishes approved public downloads.",
    owner: "Web team",
    canonical_source: "https://example.com/architectures/public-files",
    version: "1",
    scope,
    artifact_key: "artifact-key",
    artifact_name: "public-files.pdf",
    media_type: "application/pdf",
    content_hash: "a".repeat(64),
    controls: [
      {
        concept_id: id,
        concept_version_id: id,
        disposition: "exception",
        rationale:
          "The storage account intentionally serves approved public downloads.",
        approved_by: "Architecture board",
        approved_at: timestamp,
        expires_on: "2027-01-01",
      },
    ],
    recorded_at: timestamp,
  };
  await page.route("**/api/architectures", (route) =>
    route.fulfill({
      json: [{ id: architectureId, revisions: [revision] }],
    }),
  );
  await page.route(`**/api/architectures/${architectureId}`, (route) =>
    route.fulfill({
      json: { id: architectureId, revisions: [revision] },
    }),
  );
  await page.route(
    `**/api/architectures/${architectureId}/requirements`,
    (route) =>
      route.fulfill({
        json: [
          {
            control: revision.controls[0],
            concept,
          },
        ],
      }),
  );

  await page.goto("/solutions");
  await expect(
    page.getByRole("heading", {
      name: "Register solution architecture decisions.",
    }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Public files", exact: true }).click();
  await expect(page).toHaveURL(`/solutions/${architectureId}`);
  await expect(
    page.getByRole("heading", { name: "Public files", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "The storage account intentionally serves approved public downloads.",
    ),
  ).toBeVisible();
  await expect(
    page.getByText("Approved exception", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: `Download ${revision.artifact_name}` }),
  ).toHaveAttribute(
    "href",
    `/api/architectures/${architectureId}/revisions/${findingId}/artifact`,
  );
  await expect(
    page.getByRole("link", { name: statement, exact: true }),
  ).toHaveAttribute("href", `/concepts/${id}?view=architecture`);
});

test("solution page shows retained assessment findings without hiding unknowns", async ({
  page,
}) => {
  const revision = {
    id: findingId,
    architecture_id: architectureId,
    name: "Reference app",
    description: "Web app reference solution.",
    owner: "Web team",
    canonical_source: "https://example.com/architectures/reference-app",
    version: "1",
    scope,
    artifact_key: "artifact-key",
    artifact_name: "restricted.json",
    media_type: "application/json",
    content_hash: "a".repeat(64),
    controls: [],
    recorded_at: timestamp,
  };
  const finding = (
    disposition: string,
    statement: string,
    extra: Record<string, unknown> = {},
  ) => ({
    id: `${disposition}-finding`,
    requirement: {
      concept_id: id,
      version_id: id,
      statement,
      evidence_location: `version:${id}`,
    },
    disposition,
    authoritative: disposition !== "unknown",
    applicability: "applicable",
    design: disposition === "unknown" ? "unresolved" : "accepted",
    implementation: disposition === "violated" ? "failed" : "passed",
    explanation: `Explains ${disposition}.`,
    remediation: disposition === "violated" ? "Enable the plan." : null,
    decision_location: "design/restricted.json#adr-001",
    resources: ["Microsoft.Network/virtualNetworks/refapp-vnet"],
    exception: null,
    chain: [],
    ...extra,
  });
  const report = {
    target: {
      as_of: "2026-06-30",
      commit: "0123456789abcdef",
      artifact_sha256: "b".repeat(64),
      architecture_revision_id: findingId,
    },
    baseline_revision: "c".repeat(64),
    baseline_complete: true,
    findings: [
      finding("conforms", "Send audit logs to a workspace.", {
        chain: [
          {
            concept_id: relatedId,
            level: "platform_agnostic",
            statement: "Protect against denial of service.",
          },
        ],
      }),
      finding("violated", "Deploy DDoS protection."),
      finding("excepted", "Disable public network access.", {
        exception: {
          approved_by: "Architecture board",
          effective_until: "2027-01-01",
        },
      }),
      finding("unknown", "Rotate secrets automatically."),
    ],
    coverage: {
      total: 4,
      applicable: 4,
      unresolved: 1,
      violated: 1,
      excepted: 1,
      not_applicable: 0,
    },
    conforms: false,
    acceptable_with_exceptions: false,
    gaps: ["One requirement has no approved authority."],
    fingerprint: "d".repeat(64),
  };
  await page.route(`**/api/architectures/${architectureId}`, (route) =>
    route.fulfill({ json: { id: architectureId, revisions: [revision] } }),
  );
  await page.route(
    `**/api/architectures/${architectureId}/requirements`,
    (route) => route.fulfill({ json: [] }),
  );
  await page.route(
    `**/api/architectures/${architectureId}/assessments?**`,
    (route) =>
      route.fulfill({
        json: [
          {
            id: findingId,
            assessed_at: timestamp,
            baseline_concept_id: id,
            baseline_version_id: id,
            report,
          },
        ],
      }),
  );

  await page.goto(`/solutions/${architectureId}`);
  const panel = page.getByRole("main");
  await expect(panel.getByText("Conformance not established")).toBeVisible();
  await expect(panel.getByText("commit 0123456789abcdef")).toBeVisible();
  await expect(
    panel.getByText("One requirement has no approved authority."),
  ).toBeVisible();
  for (const [disposition, text] of [
    ["Conforms", "Send audit logs to a workspace."],
    ["Violated", "Deploy DDoS protection."],
    ["Excepted", "Disable public network access."],
    ["Unknown", "Rotate secrets automatically."],
  ]) {
    const row = panel.getByRole("row", { name: new RegExp(text) });
    await expect(row.getByText(disposition, { exact: true })).toBeVisible();
  }
  await expect(panel.getByText("Remediation: Enable the plan.")).toBeVisible();
  await expect(
    panel.getByText("Approved by Architecture board until 2027-01-01"),
  ).toBeVisible();
  await expect(
    panel.getByText(
      "Traces to Platform Agnostic: Protect against denial of service.",
    ),
  ).toBeVisible();
});

test("rules export prints layered evidence and never truncates silently", async ({
  page,
}) => {
  const ruleId = (index: number) =>
    `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
  const rule = (
    conceptId: string,
    level: string,
    statement: string,
    canonicalStatus: string,
    relationships: { to_concept_id: string; kind: string }[] = [],
  ) => {
    const versionId = `${conceptId.slice(0, -4)}ffff`;
    return {
      id: conceptId,
      versions: [
        {
          id: versionId,
          semantic_version: null,
          candidate: {
            semantic_type: "ConceptualRequirement",
            goal_level: null,
            statement,
            modality: "must",
            confidence: 0.9,
            uncertainty: null,
            scope: {},
          },
          publication: { publication_status: "published" },
          recorded_at: timestamp,
          assertion: null,
        },
      ],
      occurrences: [
        {
          id: `${conceptId.slice(0, -4)}eeee`,
          concept_version_id: versionId,
          excerpt: `Exact excerpt: ${statement}`,
          start: 0,
          end: 15 + statement.length,
        },
      ],
      authority_states: [
        {
          concept_version_id: versionId,
          governance_status: "approved",
          canonical_status: canonicalStatus,
          scope: {},
          effective_from: null,
          effective_until: null,
        },
      ],
      authority_events: [],
      relationships: relationships.map((item) => ({
        from_concept_id: conceptId,
        status: "proposed",
        ...item,
      })),
      levels: { [versionId]: level },
      sources: [
        {
          id: sourceId,
          name: "Security benchmark",
          publisher: "Publisher",
          location: "https://example.com/benchmark.pdf",
          version_ids: [versionId],
          evidence_counts: { [versionId]: 1 },
        },
      ],
    };
  };
  const sourceId = ruleId(9999);
  const company = rule(
    ruleId(1),
    "company",
    "Protect customer data.",
    "canonical",
  );
  const agnostic = rule(
    ruleId(2),
    "platform_agnostic",
    "Encrypt data in transit.",
    "not_canonical",
    [{ to_concept_id: company.id, kind: "supports" }],
  );
  const platform = Array.from({ length: 200 }, (_, index) =>
    rule(
      ruleId(100 + index),
      "platform_specific",
      `Control ${index}.`,
      "not_canonical",
    ),
  );
  await page.route("**/api/sources", (route) =>
    route.fulfill({
      json: [
        { item: { id: sourceId, name: "Security benchmark", metadata: {} } },
      ],
    }),
  );
  await page.route("**/api/concepts?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    const level = params.get("level");
    if (level === "company") return route.fulfill({ json: [company] });
    if (level === "platform_agnostic")
      return route.fulfill({ json: [agnostic] });
    return route.fulfill({
      json: params.get("after")
        ? [rule(ruleId(999), level ?? "", "Later.", "not_canonical")]
        : platform,
    });
  });

  await page.emulateMedia({ media: "print" });
  await page.goto("/export/rules");
  await expect(page.locator(".sidebar")).toBeHidden();
  await expect(page.getByRole("combobox", { name: "Layer" })).toBeHidden();
  const meta = page.locator(".export-meta");
  await expect(meta.getByText("Generated", { exact: true })).toBeVisible();
  await expect(meta.getByText("All layers", { exact: true })).toBeVisible();
  await expect(meta.getByText("Canonical and candidates")).toBeVisible();
  const companyItem = page.locator(`#rule-${company.id}`);
  await expect(
    companyItem.getByText("Canonical", { exact: true }),
  ).toBeVisible();
  await expect(
    companyItem.getByText("Exact excerpt: Protect customer data."),
  ).toBeVisible();
  await expect(
    companyItem.getByText(
      /Source: Security benchmark · Publisher · https:\/\/example\.com/,
    ),
  ).toBeVisible();
  const agnosticItem = page.locator(`#rule-${agnostic.id}`);
  await expect(
    agnosticItem.getByText("Candidate — not approved"),
  ).toBeVisible();
  await expect(
    agnosticItem.getByRole("link", { name: "Protect customer data." }),
  ).toHaveAttribute("href", `#rule-${company.id}`);
  await expect(
    page.getByText(/200 items · limit of 200 reached; more exist/),
  ).toBeVisible();

  await page.emulateMedia({ media: "screen" });
  await page
    .getByRole("combobox", { name: "Canonical status" })
    .selectOption("canonical");
  await expect(page).toHaveURL(/status=canonical/);
  await expect(page.locator(`#rule-${agnostic.id}`)).toHaveCount(0);
  await expect(companyItem).toBeVisible();
});

test("portfolio rates always show their applicable population", async ({
  page,
}) => {
  const bodies: unknown[] = [];
  await page.route("**/api/baselines", (route) =>
    route.fulfill({
      json: [
        {
          concept_id: id,
          version_id: id,
          source: "Baseline reference-app-baseline",
          policy_version: "1",
          statement: "Hosted web workloads apply the baseline.",
          approved: false,
        },
      ],
    }),
  );
  await page.route("**/api/portfolio/exception-risk", (route) => {
    bodies.push(route.request().postDataJSON());
    return route.fulfill({
      json: {
        policy_id: "reference-app-baseline",
        policy_version: "1",
        as_of: "2026-06-30",
        population: "Latest revision of every registered solution.",
        solutions: 5,
        solutions_without_design: 1,
        distinct_exception_decisions: 2,
        solutions_with_exceptions: 2,
        requirements: [
          {
            requirement: {
              concept_id: id,
              version_id: id,
              statement: "Deploy DDoS protection.",
              evidence_location: `version:${id}`,
            },
            authoritative_solutions: 4,
            applicable: 4,
            applicability_unknown: 1,
            active_exceptions: 1,
            rate: 0.25,
            violated: 1,
            decision_required: 0,
          },
          {
            requirement: {
              concept_id: relatedId,
              version_id: relatedId,
              statement: "Disable public network access.",
              evidence_location: `version:${relatedId}`,
            },
            authoritative_solutions: 0,
            applicable: 0,
            applicability_unknown: 0,
            active_exceptions: 0,
            rate: null,
            violated: 0,
            decision_required: 0,
          },
        ],
        signals: [],
        gaps: [],
        limitations: ["Counts are not compliance rates."],
      },
    });
  });

  await page.goto("/portfolio");
  await expect(page.getByText("Awaiting human approval")).toBeVisible();
  await expect(page.getByText("25% (1 of 4)")).toBeVisible();
  await expect(page.getByText("No applicable population")).toBeVisible();
  await expect(
    page.getByText("Counts are not compliance rates."),
  ).toBeVisible();
  // React StrictMode may repeat the effect in development; every request is scoped.
  expect(bodies.length).toBeGreaterThan(0);
  for (const body of bodies) {
    expect(body).toEqual({ baseline_concept_id: id, baseline_version_id: id });
  }
  await expect(
    page.getByText("Register a second baseline version to compare."),
  ).toBeVisible();
});

test("relationship maps retain generic occurrence evidence without inventing alignments", async ({
  page,
}) => {
  await page.route(`**/api/concepts/${id}/graph`, (route) =>
    route.fulfill({
      json: {
        root: id,
        nodes: [
          {
            id,
            label: "Protect web applications with the selected platform.",
            kind: "requirement",
            level: "platform_specific",
            href: `/concepts/${id}`,
          },
          {
            id: sourceId,
            label: source.item.name,
            kind: "source",
            level: "platform_agnostic",
            href: `/sources/${sourceId}`,
          },
          {
            id: relatedId,
            label: "A company objective awaiting alignment.",
            kind: "objective",
            level: "company",
            href: `/concepts/${relatedId}`,
          },
        ],
        edges: [
          {
            source: sourceId,
            target: id,
            kind: "evidence",
            status: "provenance",
            href: `/sources/${sourceId}`,
            evidence: {
              excerpt: statement,
              segment_id: id,
              start: 0,
              end: statement.length,
            },
            note: "Exact source occurrence, not company alignment.",
          },
        ],
        truncated: false,
      },
    }),
  );
  await page.goto(`/explore?concept=${id}`);
  const publisher = page.getByRole("link", {
    name: `Browse source ${source.item.name}`,
  });
  await expect(publisher).toBeVisible();
  await expect(
    page.getByRole("link", {
      name: /Explore A company objective awaiting alignment/,
    }),
  ).toHaveCount(0);
  await page.getByText("Link basis & evidence", { exact: true }).click();
  await expect(page.locator(".graph-record blockquote")).toHaveText(statement);
  await expect(publisher).toHaveAttribute("href", `/sources/${sourceId}`);
});

test("configured principles show the complete evidence-backed architecture chain", async ({
  page,
}) => {
  const requirementId = "55555555-5555-4555-8555-555555555555";
  const domainId = "66666666-6666-4666-8666-666666666666";
  const nistSourceId = "77777777-7777-4777-8777-777777777777";
  const curationSourceId = "88888888-8888-4888-8888-888888888888";
  const rationale =
    "Azure virtual-network peering refines NIST boundary protection within Network security in support of the NIST control baseline.";
  await page.route(`**/api/concepts/${id}`, (route) =>
    route.fulfill({
      json: {
        ...concept,
        levels: { [id]: "platform_specific" },
        versions: [
          {
            ...concept.versions[0],
            candidate: {
              ...concept.versions[0].candidate,
              semantic_type: "PlatformRequirement",
              statement: "Azure · NS-2: Use virtual network peering.",
              scope: { ...scope, platform: "Azure" },
            },
          },
        ],
      },
    }),
  );
  await page.route(`**/api/concepts/${id}/traceability`, (route) =>
    route.fulfill({
      json: {
        root: id,
        nodes: [
          {
            id,
            label: "Azure · NS-2: Use virtual network peering.",
            kind: "PlatformRequirement",
            level: "platform_specific",
            href: `/concepts/${id}`,
          },
          {
            id: relatedId,
            label:
              "Control communications at external managed interfaces; Establish a traffic flow policy for each managed interface; and Protect confidentiality across each interface.",
            kind: "TechnicalRequirement",
            level: "platform_agnostic",
            href: `/concepts/${relatedId}`,
          },
          {
            id: domainId,
            label: "Network security",
            kind: "ArchitectureDomain",
            level: "platform_agnostic",
            href: `/concepts/${domainId}`,
          },
          {
            id: requirementId,
            label:
              "Use NIST SP 800-53 Revision 5 as the cybersecurity control baseline.",
            kind: "StrategicRequirement",
            level: "company",
            href: `/concepts/${requirementId}`,
          },
          {
            id: sourceId,
            label: "Azure Security Benchmark v2",
            kind: "source",
            href: `/sources/${sourceId}`,
          },
          {
            id: nistSourceId,
            label: "NIST SP 800-53 Revision 5",
            kind: "source",
            href: `/sources/${nistSourceId}`,
          },
          {
            id: curationSourceId,
            label: "Organization architecture curation",
            kind: "source",
            href: `/sources/${curationSourceId}`,
          },
        ],
        edges: [
          {
            source: id,
            target: relatedId,
            kind: "refines",
            status: "configured",
            href: `/concepts/${id}`,
            evidence: { excerpt: rationale },
          },
          {
            source: relatedId,
            target: domainId,
            kind: "inDomain",
            status: "configured",
            href: `/concepts/${relatedId}`,
            evidence: { excerpt: rationale },
          },
          {
            source: domainId,
            target: requirementId,
            kind: "supports",
            status: "configured",
            href: `/concepts/${domainId}`,
            evidence: { excerpt: rationale },
          },
          {
            source: sourceId,
            target: id,
            kind: "evidence",
            status: "provenance",
            href: `/sources/${sourceId}`,
          },
          {
            source: nistSourceId,
            target: relatedId,
            kind: "evidence",
            status: "provenance",
            href: `/sources/${nistSourceId}`,
          },
          {
            source: curationSourceId,
            target: domainId,
            kind: "evidence",
            status: "provenance",
            href: `/sources/${curationSourceId}`,
          },
          {
            source: curationSourceId,
            target: requirementId,
            kind: "evidence",
            status: "provenance",
            href: `/sources/${curationSourceId}`,
          },
        ],
        truncated: false,
      },
    }),
  );

  await page.goto(`/concepts/${id}`);

  const traceability = page.getByRole("region", {
    name: "Traceability chain",
    exact: true,
  });
  await expect(traceability).toBeVisible();
  await expect(traceability).toContainText(
    "Use NIST SP 800-53 Revision 5 as the cybersecurity control baseline.",
  );
  await expect(traceability).toContainText("Network security");
  await expect(traceability).toContainText(
    "Control communications at external managed interfaces…",
  );
  await expect(traceability).not.toContainText(
    "Establish a traffic flow policy",
  );
  const summarized = traceability.getByRole("link", {
    name: /Control communications at external managed interfaces…/,
  });
  await expect(summarized).toContainText("+2 more · full statement");
  await expect(summarized).toHaveAttribute("href", `/concepts/${relatedId}`);
  await expect(summarized).toHaveAttribute(
    "title",
    /Protect confidentiality across each interface\.$/,
  );
  await expect(traceability).toContainText(
    "Azure · NS-2: Use virtual network peering.",
  );
  await expect(traceability).toContainText("Complete recorded path");
  await page
    .getByRole("tab", { name: "Visual validation", exact: true })
    .click();

  const full = page.getByRole("region", {
    name: "Full traceability chain",
    exact: true,
  });
  await expect(full).toBeVisible();
  for (const text of [
    "Use NIST SP 800-53 Revision 5 as the cybersecurity control baseline.",
    "Network security",
    "Control communications at external managed interfaces…",
    "Azure · NS-2: Use virtual network peering.",
  ]) {
    await expect(traceability).toContainText(text);
    await expect(full).toContainText(text);
  }
  await expect(traceability.locator(".traceability-nodes a")).toHaveCount(4);
  await expect(full.locator(".traceability-nodes a")).toHaveCount(4);
  await expect(full).toContainText("Complete recorded path");
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
