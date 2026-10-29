import { test, expect, type Page } from "@playwright/test";

const id = "11111111-1111-4111-8111-111111111111";
const findingId = "22222222-2222-4222-8222-222222222222";
const timestamp = "2026-01-01T10:00:00Z";
const scope = { environment: "test", organization: null };
const publication = {
  publication_status: "draft",
  publisher_version: "v2",
  semantic_version: null,
};
const statement = "Teams must retain audit logs.";
const excerpt = "Teams must retain <em>audit logs</em>.";
const source = {
  item: {
    id,
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
  versions: [
    {
      id,
      candidate: {
        statement,
        kind: "requirement",
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
    else if (path === "/api/sources") data = [source];
    else if (path === "/api/concepts")
      data = statement
        .toLowerCase()
        .includes((url.searchParams.get("q") ?? "").toLowerCase())
        ? [concept]
        : [];
    else if (path === `/api/concepts/${id}`) data = concept;
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
          segment: { id, heading: "Logging", location: { page: 4 } },
          occurrence: {
            id,
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

test.beforeEach(async ({ page }) => {
  await fixture(page);
});

test("overview makes attention and durable stages primary", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Knowledge, with evidence." }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Needs your attention" }),
  ).toBeVisible();
  await expect(
    page.getByText("Unresolved Duplicate", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "View queue", exact: true }),
  ).toHaveAttribute("href", "/review");
  await expect(page.getByText("Indexing", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("search, exact evidence and scoped authority remain distinct", async ({
  page,
}) => {
  await page.goto("/knowledge");
  await page.getByRole("searchbox", { name: "Search knowledge" }).fill("audit");
  await page.getByRole("link", { name: statement, exact: true }).click();
  await expect(page.locator("blockquote")).toHaveText(excerpt);
  await expect(page.locator("blockquote em")).toHaveCount(0);
  await expect(page.getByText("Exact segment offsets 5–")).toBeVisible();
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

test("issue filters and evidence drill-down do not expose write controls", async ({
  page,
}) => {
  await page.goto("/review");
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
    page.getByText("Example publisher", { exact: true }),
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

test("dark theme persists across navigation and mobile stays within its viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?scoutTheme=light");
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Toggle navigation" }).click();
  await page.getByRole("link", { name: "Knowledge", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(
    page.getByRole("link", { name: statement, exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
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
