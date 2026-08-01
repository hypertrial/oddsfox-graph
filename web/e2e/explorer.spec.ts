import { expect, test } from "@playwright/test";

const node = {
  id: "yes-1",
  label: "Alpha happens",
  level: "proposition",
  parent_id: "event-alpha",
  x: 0,
  y: 0,
  size: 8,
  domain: "sports",
  component_id: "component-one",
  market_id: "market-one",
  proposition_count: 1,
  edge_count: 0,
  classification_coverage: 1,
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown;
    if (url.pathname.endsWith("/meta")) {
      body = {
        package_version: "0.11.0",
        viewer: { build_mode: "fast", validation_status: "DETERMINISTIC_VALIDATED", evidence_tiers: ["source_contract", "deterministic_rule"] },
        coverage: { classification_coverage: 0.98, all_market_selection: true },
        build: { build_mode: "fast", validation_status: "DETERMINISTIC_VALIDATED" },
      };
    } else if (url.pathname.endsWith("/overview")) {
      body = { level: "event", nodes: [], edges: [], truncated_nodes: false, truncated_edges: false, coverage: {} };
    } else if (url.pathname.endsWith("/search")) {
      body = [{ node_id: "yes-1", market_id: "market-one", outcome_label: "Yes", event_slug: "event-alpha", canonical_proposition: "Alpha happens" }];
    } else if (url.pathname.endsWith("/subgraph")) {
      body = { level: "proposition", nodes: [node], edges: [], truncated_nodes: false, truncated_edges: false, coverage: {} };
    } else if (url.pathname.includes("/nodes/")) {
      body = { node, edges: [] };
    } else if (url.pathname.endsWith("/prove")) {
      body = [{ from_node_id: "yes-1", to_node_id: "yes-2", steps: [], hops: 1, bottleneck_confidence: 0.99 }];
    } else if (url.pathname.endsWith("/why-not")) {
      body = { status: "not_retrieved", explanation: "Pair was not retrieved" };
    } else {
      body = {};
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
});

test("explores a result and runs bounded reasoning tools", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Logic Explorer" })).toBeVisible();
  await expect(page.getByText("98.0%" )).toBeVisible();

  await page.getByLabel("Search markets and propositions").fill("Alpha");
  await page.getByRole("button", { name: "Search" }).click();
  await page.getByRole("button", { name: /Alpha happens/ }).click();
  await expect(page.getByText("yes-1", { exact: true })).toBeVisible();

  await page.getByLabel("To proposition").fill("yes-2");
  await page.getByRole("button", { name: "Prove path" }).click();
  await expect(page.getByText(/bottleneck_confidence/)).toBeVisible();

  await page.getByRole("button", { name: "Explain absence" }).click();
  await expect(page.getByText(/not_retrieved/)).toBeVisible();
});
